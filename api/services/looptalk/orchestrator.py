import asyncio
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, Optional

from loguru import logger

from api.db.db_client import DBClient
from api.services.looptalk.internal_transport import (
    InternalTransport,
    InternalTransportManager,
)
from api.services.pipecat.transport_setup import create_internal_transport
from pipecat.pipeline.task import PipelineTask
from pipecat.utils.context import set_current_run_id

from .core.pipeline_builder import LoopTalkPipelineBuilder
from .core.recording_manager import RecordingManager
from .core.session_manager import SessionManager


class LoopTalkTestOrchestrator:
    """Orchestrates LoopTalk testing sessions with agent-to-agent conversations."""

    def __init__(
        self, db_client: DBClient, network_latency_seconds: Optional[float] = None
    ):
        self.db_client = db_client
        self.transport_manager = InternalTransportManager()
        self.session_manager = SessionManager()
        self.pipeline_builder = LoopTalkPipelineBuilder(db_client)
        self.recording_manager = RecordingManager(Path("/tmp/looptalk_recordings"))

        # Default network latency (can be overridden per session)
        # Priority: constructor param > env var > default (100ms)
        if network_latency_seconds is not None:
            self._default_network_latency = network_latency_seconds
        else:
            env_latency = os.environ.get("LOOPTALK_NETWORK_LATENCY_MS")
            if env_latency:
                try:
                    self._default_network_latency = (
                        float(env_latency) / 1000.0
                    )  # Convert ms to seconds
                except ValueError:
                    logger.warning(
                        f"Invalid LOOPTALK_NETWORK_LATENCY_MS value: {env_latency}, using default 100ms"
                    )
                    self._default_network_latency = 0.1
            else:
                self._default_network_latency = 0.1  # 100ms default

    async def start_test_session(
        self,
        test_session_id: int,
        organization_id: int,
        network_latency_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Start a LoopTalk test session."""

        # Get test session details
        test_session = await self.db_client.get_test_session(
            test_session_id=test_session_id, organization_id=organization_id
        )

        if not test_session:
            raise ValueError(f"Test session {test_session_id} not found")

        if test_session.status != "pending":
            raise ValueError(f"Test session {test_session_id} is not in pending state")

        try:
            # Update status to running
            await self.db_client.update_test_session_status(
                test_session_id=test_session_id, status="running"
            )

            # Create conversation record
            conversation = await self.db_client.create_conversation(
                test_session_id=test_session_id
            )

            # Create audio configuration for LoopTalk
            from api.services.pipecat.audio_config import AudioConfig

            audio_config = AudioConfig(
                transport_in_sample_rate=16000,
                transport_out_sample_rate=16000,
                pipeline_sample_rate=16000,
            )

            # Use provided latency or fall back to default
            latency = (
                network_latency_seconds
                if network_latency_seconds is not None
                else self._default_network_latency
            )
            logger.info(
                f"Using network latency of {latency}s for test session {test_session_id}"
            )

            # Generate unique workflow run IDs for each agent
            actor_workflow_run_id = int(str(test_session_id) + "1")
            adversary_workflow_run_id = int(str(test_session_id) + "2")

            # Create transports using the new method with turn analyzer
            actor_transport = create_internal_transport(
                workflow_run_id=actor_workflow_run_id,
                audio_config=audio_config,
                latency_seconds=latency,
            )
            adversary_transport = create_internal_transport(
                workflow_run_id=adversary_workflow_run_id,
                audio_config=audio_config,
                latency_seconds=latency,
            )

            # Connect the transports
            actor_transport.connect_partner(adversary_transport)

            # Store the transport pair in the manager
            self.transport_manager._transport_pairs[str(test_session_id)] = (
                actor_transport,
                adversary_transport,
            )

            # Generate unique identifiers for actor and adversary
            actor_id = f"actor_{test_session_id}_{str(uuid.uuid4())[:8]}"
            adversary_id = f"adversary_{test_session_id}_{str(uuid.uuid4())[:8]}"

            # Create pipelines for both agents
            actor_pipeline_info = await self.pipeline_builder.create_agent_pipeline(
                transport=actor_transport,
                workflow=test_session.actor_workflow,
                test_session_id=test_session_id,
                agent_id=actor_id,
                role="actor",
            )
            actor_pipeline_task = actor_pipeline_info["task"]

            adversary_pipeline_info = await self.pipeline_builder.create_agent_pipeline(
                transport=adversary_transport,
                workflow=test_session.adversary_workflow,
                test_session_id=test_session_id,
                agent_id=adversary_id,
                role="adversary",
            )

            adversary_pipeline_task = adversary_pipeline_info["task"]

            # Register event handlers for both pipelines
            await self._register_transport_handlers(
                actor_transport, actor_pipeline_info, test_session_id, "actor"
            )
            await self._register_transport_handlers(
                adversary_transport,
                adversary_pipeline_info,
                test_session_id,
                "adversary",
            )

            # Store session info
            session_info = {
                "test_session": test_session,
                "conversation": conversation,
                "actor_task": actor_pipeline_task,
                "adversary_task": adversary_pipeline_task,
                "actor_transport": actor_transport,
                "adversary_transport": adversary_transport,
                "start_time": datetime.now(UTC),
            }
            self.session_manager.add_session(test_session_id, session_info)

            # Start both pipelines in background tasks
            from pipecat.pipeline.base_task import PipelineTaskParams

            params = PipelineTaskParams(loop=asyncio.get_event_loop())

            # Start the pipelines - this will trigger initialization through the normal pipeline start process
            # The workflow engines will be initialized when the pipeline starts

            # Create conversation IDs for tracing
            actor_conversation_id = f"{test_session_id}-actor-{actor_id}"
            adversary_conversation_id = f"{test_session_id}-adversary-{adversary_id}"

            # Create tasks but don't await them - they'll run in the background
            logger.debug(f"Running actor task with ID: {actor_id}")
            actor_task_future = asyncio.create_task(
                self._run_pipeline_with_context(
                    actor_pipeline_task,
                    params,
                    actor_id,
                    actor_conversation_id,
                    "actor",
                )
            )

            logger.debug(f"Running adversary task with ID: {adversary_id}")
            adversary_task_future = asyncio.create_task(
                self._run_pipeline_with_context(
                    adversary_pipeline_task,
                    params,
                    adversary_id,
                    adversary_conversation_id,
                    "adversary",
                )
            )

            # Store the futures so we can monitor them
            session_info["actor_task_future"] = actor_task_future
            session_info["adversary_task_future"] = adversary_task_future

            logger.info(f"Started LoopTalk test session {test_session_id}")

            return {
                "test_session_id": test_session_id,
                "conversation_id": conversation.id,
                "status": "running",
            }

        except Exception as e:
            logger.error(f"Failed to start test session {test_session_id}: {e}")
            await self.db_client.update_test_session_status(
                test_session_id=test_session_id, status="failed", error=str(e)
            )
            raise

    async def _register_transport_handlers(
        self,
        transport: InternalTransport,
        pipeline_info: Dict[str, Any],
        test_session_id: int,
        role: str,
    ):
        """Register transport event handlers for a pipeline.

        Args:
            transport: The transport to register handlers on
            pipeline_info: Dictionary containing pipeline components
            test_session_id: ID of the test session
            role: Either "actor" or "adversary"
        """
        engine = pipeline_info["engine"]
        task = pipeline_info["task"]
        audio_buffer = pipeline_info["audio_buffer"]
        audio_synchronizer = pipeline_info["audio_synchronizer"]
        transcript = pipeline_info["transcript"]
        assistant_context_aggregator = pipeline_info["assistant_context_aggregator"]

        # Register transport event handlers
        @transport.event_handler("on_client_connected")
        async def on_client_connected(transport, participant):
            logger.debug(f"LoopTalk {role} client connected - initializing workflow")
            # Start audio recording
            await audio_buffer.start_recording()
            await audio_synchronizer.start_recording()
            await engine.initialize()

        @transport.event_handler("on_client_disconnected")
        async def on_client_disconnected(transport, participant):
            logger.debug(f"LoopTalk {role} client disconnected")
            # Stop audio recording
            await audio_buffer.stop_recording()
            await audio_synchronizer.stop_recording()

            # Handle disconnect propagation - stop the other agent too
            await self.session_manager.handle_agent_disconnect(
                test_session_id, role, self.stop_test_session
            )

            await task.cancel()

        # Connect the context aggregator events to engine
        @assistant_context_aggregator.event_handler("on_push_aggregation")
        async def on_assistant_aggregator_push_context(_aggregator):
            logger.debug(
                "Assistant aggregator push context – flushing pending transitions"
            )
            await engine.flush_pending_transitions()

        # Register custom audio and transcript handlers for LoopTalk
        await self._register_looptalk_handlers(
            audio_synchronizer, transcript, test_session_id, role
        )

    async def _register_looptalk_handlers(
        self, audio_synchronizer, transcript, test_session_id: int, role: str
    ):
        """Register LoopTalk-specific handlers for audio and transcript recording"""

        paths = self.recording_manager.get_recording_paths(test_session_id, role)

        # Store audio metadata for later WAV conversion
        audio_metadata = {"sample_rate": None, "num_channels": None}

        # Audio handler - writes directly to PCM file
        @audio_synchronizer.event_handler("on_merged_audio")
        async def on_merged_audio(_, pcm, sample_rate, num_channels):
            if not pcm:
                return

            # Store metadata on first write
            if audio_metadata["sample_rate"] is None:
                audio_metadata["sample_rate"] = sample_rate
                audio_metadata["num_channels"] = num_channels

            # Append PCM data to temporary file
            try:
                with open(paths["temp_audio"], "ab") as f:
                    f.write(pcm)
            except Exception as e:
                logger.error(
                    f"Failed to write audio for {role} in session {test_session_id}: {e}"
                )

        # Transcript handler - writes directly to text file
        @transcript.event_handler("on_transcript_update")
        async def on_transcript_update(processor, frame):
            transcript_text = ""
            for msg in frame.messages:
                timestamp = f"[{msg.timestamp}] " if msg.timestamp else ""
                line = f"{timestamp}{msg.role}: {msg.content}\n"
                transcript_text += line

            # Append transcript to file
            try:
                with open(paths["transcript"], "a") as f:
                    f.write(transcript_text)
            except Exception as e:
                logger.error(
                    f"Failed to write transcript for {role} in session {test_session_id}: {e}"
                )

        # Store metadata in session info for later WAV conversion
        # Set default values if not yet captured
        if audio_metadata["sample_rate"] is None:
            audio_metadata["sample_rate"] = 16000  # Default sample rate
            audio_metadata["num_channels"] = 1  # Default channels

        self.session_manager.update_audio_metadata(
            test_session_id,
            role,
            sample_rate=audio_metadata["sample_rate"],
            num_channels=audio_metadata["num_channels"],
        )

    async def _run_pipeline_with_context(
        self,
        pipeline_task: PipelineTask,
        params,
        agent_id: str,
        conversation_id: str,
        role: str,
    ):
        """Run a pipeline task with the agent_id set in context"""
        set_current_run_id(agent_id)
        return await pipeline_task.run(params)

    async def stop_test_session(self, test_session_id: int) -> Dict[str, Any]:
        """Stop a running test session."""

        session_info = self.session_manager.get_session(test_session_id)
        if not session_info:
            raise ValueError(f"Test session {test_session_id} is not running")

        try:
            # Cancel both pipeline tasks
            await session_info["actor_task"].cancel()
            await session_info["adversary_task"].cancel()

            # Also cancel the task futures if they exist
            if "actor_task_future" in session_info:
                session_info["actor_task_future"].cancel()
            if "adversary_task_future" in session_info:
                session_info["adversary_task_future"].cancel()

            # Calculate duration
            duration_seconds = int(
                (datetime.now(UTC) - session_info["start_time"]).total_seconds()
            )

            # Update conversation
            await self.db_client.update_conversation(
                conversation_id=session_info["conversation"].id,
                duration_seconds=duration_seconds,
                ended_at=datetime.now(UTC),
            )

            # Update test session status
            await self.db_client.update_test_session_status(
                test_session_id=test_session_id,
                status="completed",
                results={
                    "duration_seconds": duration_seconds,
                    "conversation_id": session_info["conversation"].id,
                },
            )

            # Finalize recordings for both actor and adversary
            # Convert PCM files to WAV
            actor_metadata = self.session_manager.get_audio_metadata(
                test_session_id, "actor"
            )
            adversary_metadata = self.session_manager.get_audio_metadata(
                test_session_id, "adversary"
            )

            self.recording_manager.convert_pcm_to_wav(
                test_session_id,
                "actor",
                sample_rate=actor_metadata["sample_rate"],
                num_channels=actor_metadata["num_channels"],
            )
            self.recording_manager.convert_pcm_to_wav(
                test_session_id,
                "adversary",
                sample_rate=adversary_metadata["sample_rate"],
                num_channels=adversary_metadata["num_channels"],
            )

            # Upload recordings to S3 (synchronously for load testing)
            (
                actor_audio_url,
                actor_transcript_url,
            ) = await self.recording_manager.upload_recording_to_s3(
                test_session_id, "actor"
            )
            (
                adversary_audio_url,
                adversary_transcript_url,
            ) = await self.recording_manager.upload_recording_to_s3(
                test_session_id, "adversary"
            )

            # Update conversation with recording URLs
            await self.db_client.update_conversation(
                conversation_id=session_info["conversation"].id,
                actor_recording_url=actor_audio_url,
                adversary_recording_url=adversary_audio_url,
                transcript={
                    "actor_transcript_url": actor_transcript_url,
                    "adversary_transcript_url": adversary_transcript_url,
                },
            )

            # Log recording locations
            logger.info(f"LoopTalk recordings uploaded to S3:")
            if actor_audio_url:
                logger.info(f"  - Actor audio: {actor_audio_url}")
            if actor_transcript_url:
                logger.info(f"  - Actor transcript: {actor_transcript_url}")
            if adversary_audio_url:
                logger.info(f"  - Adversary audio: {adversary_audio_url}")
            if adversary_transcript_url:
                logger.info(f"  - Adversary transcript: {adversary_transcript_url}")

            # Clean up local files after successful upload
            self.recording_manager.cleanup_session_files(test_session_id)

            # Clean up
            self.transport_manager.remove_transport_pair(str(test_session_id))
            self.session_manager.remove_session(test_session_id)

            # Clean up audio streamers
            from api.services.looptalk.audio_streamer import cleanup_audio_streamers

            cleanup_audio_streamers(str(test_session_id))

            logger.info(f"Stopped LoopTalk test session {test_session_id}")

            return {
                "test_session_id": test_session_id,
                "status": "completed",
                "duration_seconds": duration_seconds,
            }

        except Exception as e:
            logger.error(f"Failed to stop test session {test_session_id}: {e}")
            await self.db_client.update_test_session_status(
                test_session_id=test_session_id, status="failed", error=str(e)
            )
            raise

    async def start_load_test(
        self,
        organization_id: int,
        name_prefix: str,
        actor_workflow_id: int,
        adversary_workflow_id: int,
        config: Dict[str, Any],
        test_count: int,
    ) -> Dict[str, Any]:
        """Start a load test with multiple concurrent test sessions."""

        # Validate test count
        if test_count < 1 or test_count > 10:
            raise ValueError("Test count must be between 1 and 10")

        # Create test sessions
        test_sessions = await self.db_client.create_load_test_group(
            organization_id=organization_id,
            name_prefix=name_prefix,
            actor_workflow_id=actor_workflow_id,
            adversary_workflow_id=adversary_workflow_id,
            config=config,
            test_count=test_count,
        )

        # Start all test sessions concurrently
        tasks = []
        for test_session in test_sessions:
            task = asyncio.create_task(
                self.start_test_session(
                    test_session_id=test_session.id, organization_id=organization_id
                )
            )
            tasks.append(task)

        # Wait for all to start
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Count successes and failures
        started = sum(1 for r in results if not isinstance(r, Exception))
        failed = sum(1 for r in results if isinstance(r, Exception))

        load_test_group_id = test_sessions[0].load_test_group_id

        logger.info(
            f"Started load test {load_test_group_id}: "
            f"{started} started, {failed} failed out of {test_count}"
        )

        return {
            "load_test_group_id": load_test_group_id,
            "total": test_count,
            "started": started,
            "failed": failed,
            "test_session_ids": [ts.id for ts in test_sessions],
        }

    def get_active_test_count(self) -> int:
        """Get the number of currently active test sessions."""
        return self.session_manager.get_active_count()

    def get_active_test_info(self) -> Dict[str, Any]:
        """Get information about all active test sessions."""
        return self.session_manager.get_active_info()

    def get_recording_info(self, test_session_id: int) -> Dict[str, Any]:
        """Get information about recordings for a test session"""
        return self.recording_manager.get_recording_info(test_session_id)
