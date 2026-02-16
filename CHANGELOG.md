# Changelog

## [1.14.0](https://github.com/dograh-hq/dograh/compare/dograh-v1.13.0...dograh-v1.14.0) (2026-02-16)


### Features

* telephony call transfer ([#155](https://github.com/dograh-hq/dograh/issues/155)) ([c711920](https://github.com/dograh-hq/dograh/commit/c71192016561333a109393186cf0d3b70bbd894d))


### Bug Fixes

* add check for workflow run mode in transfer call ([#160](https://github.com/dograh-hq/dograh/issues/160)) ([67e92e6](https://github.com/dograh-hq/dograh/commit/67e92e6b9c508b31db4e5256f3d6afb2aadd0eb4))
* limit cloudonix transport to 20 ms packets ([559c0ca](https://github.com/dograh-hq/dograh/commit/559c0ca767389b1212b5a4726338df127a8d630a))
* llm generation to annouce failed transfer call ([28eaa93](https://github.com/dograh-hq/dograh/commit/28eaa934f3430aacd5d0151e07e8ae86274a4148))

## [1.13.0](https://github.com/dograh-hq/dograh/compare/dograh-v1.12.0...dograh-v1.13.0) (2026-02-13)


### Features

* add languages for deepgram and dograh ([5256010](https://github.com/dograh-hq/dograh/commit/525601088accb763aa710ff6e0fb2a6fdf5acec2))
* add openrouter support ([4c936ae](https://github.com/dograh-hq/dograh/commit/4c936ae57d14ea3aa40ea427b20b341776a9be9f))
* add sarvam v3 voices ([a75bc72](https://github.com/dograh-hq/dograh/commit/a75bc72cb59537098b3b605a012a2ef8a3f0fe6a))
* limit campaign concurrency to number of CLIs ([3cdede0](https://github.com/dograh-hq/dograh/commit/3cdede0f45de5d719c17797635464742ad2eac86))


### Bug Fixes

* add vad_analyzer in user aggregator ([6711dcb](https://github.com/dograh-hq/dograh/commit/6711dcb3ea7b7f78d7db5794fefc87380b8b751b))
* fix cloudonix call hangup ([#154](https://github.com/dograh-hq/dograh/issues/154)) ([b9ddd30](https://github.com/dograh-hq/dograh/commit/b9ddd308134056db4d24a37702d151c9ee45e72d))
* fixes aggregation in elevenlabs TTS ([#153](https://github.com/dograh-hq/dograh/issues/153)) ([e156524](https://github.com/dograh-hq/dograh/commit/e1565246fa6ecc5b73a663e9251a92d0796d19d3))
* send sample rate to STT services ([7a10202](https://github.com/dograh-hq/dograh/commit/7a102026fbe90bed89455919ed1f9912cbab634b))

## [1.12.0](https://github.com/dograh-hq/dograh/compare/dograh-v1.11.2...dograh-v1.12.0) (2026-02-05)


### Features

* add coturn configurations ([#143](https://github.com/dograh-hq/dograh/issues/143)) ([bf972fc](https://github.com/dograh-hq/dograh/commit/bf972fcfecc790045daa70c52b7d76dd7b4a5ca5))
* add dictionary support for STT boosting in voice agents ([#136](https://github.com/dograh-hq/dograh/issues/136)) ([db75d90](https://github.com/dograh-hq/dograh/commit/db75d905358dfe42f06b6deaf1327bec3980a87d))
* add retry config during campaign creation ([6f41e91](https://github.com/dograh-hq/dograh/commit/6f41e91f67cbd79ab106936dccb42074c21910ce))
* allow turn credentials fetching from embed agent ([6ccc649](https://github.com/dograh-hq/dograh/commit/6ccc6492eece6f6f3114b6065dab4f00281ae6ab))
* check for duplicate phone number in campaign ([814271e](https://github.com/dograh-hq/dograh/commit/814271e7b1452f7674e9ab1d5b79dc381b71708f))
* mute on function call ([#138](https://github.com/dograh-hq/dograh/issues/138)) ([9191176](https://github.com/dograh-hq/dograh/commit/91911769b0f5b2a308d71d052f44efa9839bcb2a))


### Bug Fixes

* add cloudonix CDR handling ([#140](https://github.com/dograh-hq/dograh/issues/140)) ([b1c982a](https://github.com/dograh-hq/dograh/commit/b1c982a52e27c95bf13718181bf5e4c946edf5b7))
* add error in cloudonix cdr report ([e9c5da1](https://github.com/dograh-hq/dograh/commit/e9c5da16c5873b1f078cd608afd2036a1d9abdc5))
* allow interruption on start_node ([7e438ad](https://github.com/dograh-hq/dograh/commit/7e438ad049348fcfcab0e4af748c7881858a97ec))
* BACKEND_API_ENDPOINT resolution from env and cloudflared tunnel ([#135](https://github.com/dograh-hq/dograh/issues/135)) ([4a8e4fe](https://github.com/dograh-hq/dograh/commit/4a8e4fe7a1cb783d79a9b50d52f35045f13431ec))
* better error handling for telephony ([8c42866](https://github.com/dograh-hq/dograh/commit/8c42866f80f72625a368bd135e54317e68509502))
* fix remote deployment method  ([#145](https://github.com/dograh-hq/dograh/issues/145)) ([87fc64d](https://github.com/dograh-hq/dograh/commit/87fc64d55caa290aa4b6c6605161a74abc0cb66b))
* make campaign process batch thread safe ([#141](https://github.com/dograh-hq/dograh/issues/141)) ([6827744](https://github.com/dograh-hq/dograh/commit/682774432778ecd72601d6993fb26cc3b179e1b3))

## [1.11.2](https://github.com/dograh-hq/dograh/compare/dograh-v1.11.1...dograh-v1.11.2) (2026-01-27)


### Bug Fixes

* fix variable extraction during pipeline execution flow ([6b408e5](https://github.com/dograh-hq/dograh/commit/6b408e588c61ad2a3d61593f48d19371bf090d7d))
* remove duplicate index addition in migration ([#129](https://github.com/dograh-hq/dograh/issues/129)) ([2aedb83](https://github.com/dograh-hq/dograh/commit/2aedb839ffd24377eb8b5b996cb7c36e7d8b7321))

## [1.11.1](https://github.com/dograh-hq/dograh/compare/dograh-v1.11.0...dograh-v1.11.1) (2026-01-24)


### Bug Fixes

* free disk space for docker build ([#126](https://github.com/dograh-hq/dograh/issues/126)) ([be50a24](https://github.com/dograh-hq/dograh/commit/be50a244f6bdcd47beb920bd1acc46c7a0e6608d))

## [1.11.0](https://github.com/dograh-hq/dograh/compare/dograh-v1.10.0...dograh-v1.11.0) (2026-01-23)


### Features

* add end_call tool ([#118](https://github.com/dograh-hq/dograh/issues/118)) ([a172db8](https://github.com/dograh-hq/dograh/commit/a172db8022469631826a3198382518c26e812c72))
* add rtf in logs ([#119](https://github.com/dograh-hq/dograh/issues/119)) ([cac2587](https://github.com/dograh-hq/dograh/commit/cac25879bf0ca62385bd7ba3444b911ef67494bc))
* add transcript panel during live call for better visibility ([#116](https://github.com/dograh-hq/dograh/issues/116)) ([e771247](https://github.com/dograh-hq/dograh/commit/e7712474c14701ff415881d0faff76d62ae354e7))
* add voices in Dograh configuration ([c58aa55](https://github.com/dograh-hq/dograh/commit/c58aa557de9de9ddf7904029a977a8ead9a4bb27))
* handle cloudonix incoming calls ([#121](https://github.com/dograh-hq/dograh/issues/121)) ([e2fa4bb](https://github.com/dograh-hq/dograh/commit/e2fa4bbb98bba5cc6ef9c568e686b6c1097805dd))
* knowledge base functionality for the voice agent ([#120](https://github.com/dograh-hq/dograh/issues/120)) ([ef5b9e4](https://github.com/dograh-hq/dograh/commit/ef5b9e40a90703c17c23f2d003045df28deb490e))


### Bug Fixes

* changes to update pipecat version to 0.0.100 ([#122](https://github.com/dograh-hq/dograh/issues/122)) ([911c5ed](https://github.com/dograh-hq/dograh/commit/911c5ed4169d6058f098c98b54cc57c602f09357))
* fix npm run build ([692ef27](https://github.com/dograh-hq/dograh/commit/692ef27751352922f8fbd3c14a5b4877c11cdb66))
* fix OPENAI_API_KEY bug in retrieval ([d35eeb1](https://github.com/dograh-hq/dograh/commit/d35eeb1b7b3ddc449adbee0243c2745103d86f2c))
* fix release please ([4c073b7](https://github.com/dograh-hq/dograh/commit/4c073b7894008076ad373ef4944a891b93b4e68e))
* make embeddings api key optional ([3b614b8](https://github.com/dograh-hq/dograh/commit/3b614b8b8252cb127f2792da02d408f6aad25ab1))
* set_node during node execution ([a4367bd](https://github.com/dograh-hq/dograh/commit/a4367bd83beaf15b96abc5e7ca4303ac31595d42))


### Documentation

* inbound telephony ([#124](https://github.com/dograh-hq/dograh/issues/124)) ([b996cb8](https://github.com/dograh-hq/dograh/commit/b996cb80cf84e1e48c13de1af5c84166c6b45e0a))

## [1.10.0](https://github.com/dograh-hq/dograh/compare/dograh-v1.9.0...dograh-v1.10.0) (2026-01-13)


### Features

* enable api key access to routes ([05ead4d](https://github.com/dograh-hq/dograh/commit/05ead4dc867062e61702fbf7ec82cce4bb08c9e5))
* enable Sarvam Models ([514d9c5](https://github.com/dograh-hq/dograh/commit/514d9c5238029bab4dfcca19cd7564b34d54f51c))


### Bug Fixes

* API calling endpoint ([df2bfd7](https://github.com/dograh-hq/dograh/commit/df2bfd74299839f7c851a67db5f45d93b3e8f9e6))
* catch initiate call exception in public agent ([92bdfd6](https://github.com/dograh-hq/dograh/commit/92bdfd6caccfab92397045e19800842257ed0600))
* formatting fix and fix [#79](https://github.com/dograh-hq/dograh/issues/79) ([11e033c](https://github.com/dograh-hq/dograh/commit/11e033c72d01d71b209fe0a47620fd7441fdadb4))
* initialize engine earlier than event handler ([b79bc42](https://github.com/dograh-hq/dograh/commit/b79bc4221db02a23eafd586feb75a271df065d13))
* migrate from custom audio recorder to native AudioBuffer ([#115](https://github.com/dograh-hq/dograh/issues/115)) ([edf0fa4](https://github.com/dograh-hq/dograh/commit/edf0fa4fbc98f246174b672df928c51baa9b1cab))


### Documentation

* fix broker internal link ([#114](https://github.com/dograh-hq/dograh/issues/114)) ([3152100](https://github.com/dograh-hq/dograh/commit/31521008cfacafe4ac3f55d3032608b3873a2d2c))
* tracing with langfuse ([#112](https://github.com/dograh-hq/dograh/issues/112)) ([d41f696](https://github.com/dograh-hq/dograh/commit/d41f696f3f373a72b107fab8c4d8183f23cebf2e))

## [1.9.0](https://github.com/dograh-hq/dograh/compare/dograh-v1.8.0...dograh-v1.9.0) (2026-01-03)


### Features

* add cloudonix outbound telephony ([#101](https://github.com/dograh-hq/dograh/issues/101)) ([90b690e](https://github.com/dograh-hq/dograh/commit/90b690efff0931f73ca588aa70dad7bb2f9ff797))
* add keyboard shortcut for save ([fec8da9](https://github.com/dograh-hq/dograh/commit/fec8da9d20bbce0c73d6296c7c2e74468a215bd1))
* add trace URL in workflow runs ([cdf6853](https://github.com/dograh-hq/dograh/commit/cdf68533add17d251273bd5f8d8ba1cb82d144fc))
* add voice selectors in elevenlabs ([#88](https://github.com/dograh-hq/dograh/issues/88)) ([45c5b7c](https://github.com/dograh-hq/dograh/commit/45c5b7c304f6c0e5c4d6846c3591051588f73a32))
* user defined custom tools as part of workflow execution ([#94](https://github.com/dograh-hq/dograh/issues/94)) ([3e55af9](https://github.com/dograh-hq/dograh/commit/3e55af9256ecbde4b92a547fc9bca8a8c5e57f15))


### Bug Fixes

* change type definition from enum to str for consistency ([e83f3a3](https://github.com/dograh-hq/dograh/commit/e83f3a36d2b422171113ecdf6432d8aaf9c63789))
* fix configuration option ([74b0693](https://github.com/dograh-hq/dograh/commit/74b069354b721e310a7d5b9cd93a4666ec197e5c))
* fix db filters ([de09f1c](https://github.com/dograh-hq/dograh/commit/de09f1c5016bf4b4cb44b58a6526f6b51e3389c1))
* fix links ([480e8a5](https://github.com/dograh-hq/dograh/commit/480e8a5f602583c000c1ecc0e1643024fe1a4757))
* fix migration version ([9adc766](https://github.com/dograh-hq/dograh/commit/9adc766f3c593354aac10129fabcd27856c90b88))
* llm generation in case of user idle ([04576ac](https://github.com/dograh-hq/dograh/commit/04576ac3570beffb92d0c980448b8fa67b051eb5))


### Documentation

* add page for Workflow editing basics ([#93](https://github.com/dograh-hq/dograh/issues/93)) ([3afae6c](https://github.com/dograh-hq/dograh/commit/3afae6cf099555734a96f681e98999719626b61e))
* add webhook tutorial in mintilify docs ([#92](https://github.com/dograh-hq/dograh/issues/92)) ([0b07319](https://github.com/dograh-hq/dograh/commit/0b073195f1c650a71fea7a7d2810f038cd5393b7))
* modify tools section ([#103](https://github.com/dograh-hq/dograh/issues/103)) ([a33fa6c](https://github.com/dograh-hq/dograh/commit/a33fa6cffe8178666cd49c7df3bb47a39a952604))
* tools in workflow node ([#102](https://github.com/dograh-hq/dograh/issues/102)) ([db89aed](https://github.com/dograh-hq/dograh/commit/db89aed377878031b06eb182af548ca0e19ef422))

## [1.8.0](https://github.com/dograh-hq/dograh/compare/dograh-v1.7.1...dograh-v1.8.0) (2025-12-22)


### Features

* add coturn for remote deployments ([#84](https://github.com/dograh-hq/dograh/issues/84)) ([1740999](https://github.com/dograh-hq/dograh/commit/17409998d2ebf4bec64b1418b423cdc789c40b19))
* add smart turn v3 ([4640f69](https://github.com/dograh-hq/dograh/commit/4640f69f9bee0d5094dab2ca5a5794ed007e1395))
* add voices to elevenlabs ([94b7d7e](https://github.com/dograh-hq/dograh/commit/94b7d7e2f224789a68337e7a13a44925df63a1c8))


### Bug Fixes

* add text filter for tts and logs for filter ([#74](https://github.com/dograh-hq/dograh/issues/74)) ([0a8ce3f](https://github.com/dograh-hq/dograh/commit/0a8ce3f644821f7b75d607d9c37753b632f14f0e))
* call_id and stream_id for vobiz pipeline, add workflow run state ([#78](https://github.com/dograh-hq/dograh/issues/78)) ([c99bd29](https://github.com/dograh-hq/dograh/commit/c99bd29ef18467ffa80d6da13f5f23ee7d0bbddf))
* fixes wrong selection in model config dropdown ([#80](https://github.com/dograh-hq/dograh/issues/80)) ([2e37c89](https://github.com/dograh-hq/dograh/commit/2e37c89310f739b3c0674a12e4a18ccefb3853d9))
* prevent pipeline freezes when sending endframe ([#77](https://github.com/dograh-hq/dograh/issues/77)) ([909c258](https://github.com/dograh-hq/dograh/commit/909c258b6a807bddd26dd61d3d31bddf9ee60d6f))
* use config for turn ([4ddb144](https://github.com/dograh-hq/dograh/commit/4ddb144dd00d753160fd0d7ffe962d32b9929d7f))

## [1.7.1](https://github.com/dograh-hq/dograh/compare/dograh-v1.7.0...dograh-v1.7.1) (2025-12-01)


### Bug Fixes

* fix pointer events on phone call dialog ([#70](https://github.com/dograh-hq/dograh/issues/70)) ([713c35d](https://github.com/dograh-hq/dograh/commit/713c35df649445721033e1a80f3d3d1b44933ac9))

## [1.7.0](https://github.com/dograh-hq/dograh/compare/dograh-v1.6.0...dograh-v1.7.0) (2025-11-29)


### Features

* added vobiz telephony ([#65](https://github.com/dograh-hq/dograh/issues/65)) ([09897cb](https://github.com/dograh-hq/dograh/commit/09897cb5d8d04e196f209176ce239ecbcd938ef6))
* Update Dograh's UI Design ([#67](https://github.com/dograh-hq/dograh/issues/67)) ([a7f2238](https://github.com/dograh-hq/dograh/commit/a7f2238044e480b4fd12d31679ae9e7b2a59dda8))


### Bug Fixes

* set provider during campaign run ([#69](https://github.com/dograh-hq/dograh/issues/69)) ([8342cd1](https://github.com/dograh-hq/dograh/commit/8342cd1ddaac7ed6e9c6f17fe3040a77ff67326d))

## [1.6.0](https://github.com/dograh-hq/dograh/compare/dograh-v1.5.0...dograh-v1.6.0) (2025-11-26)


### Features

* add llm models in Dograh ([#64](https://github.com/dograh-hq/dograh/issues/64)) ([a7bf64a](https://github.com/dograh-hq/dograh/commit/a7bf64a02b8de226d7d28bd42f9e96cec0b94edc))
* add new elevenlabs voices ([d60c020](https://github.com/dograh-hq/dograh/commit/d60c0206d152161741a6bdbb399861f7facb3d21))
* allow www domain for embedded websites ([#60](https://github.com/dograh-hq/dograh/issues/60)) ([ed3ceaf](https://github.com/dograh-hq/dograh/commit/ed3ceaf5ad4aedc76ba0c7f2f2fe353c0920a870))
* show error if quota is exceeded ([#66](https://github.com/dograh-hq/dograh/issues/66)) ([145da30](https://github.com/dograh-hq/dograh/commit/145da30b57fb4149c6e0f61eb8bf39164c928838))


### Bug Fixes

* permission in slack announcements action ([c37fbcd](https://github.com/dograh-hq/dograh/commit/c37fbcd7bc5378d1aefda0807f4690c1992be12e))
* slack message body ([#59](https://github.com/dograh-hq/dograh/issues/59)) ([5ab5c1d](https://github.com/dograh-hq/dograh/commit/5ab5c1d0c160b6a1e4a9b0c4beabe4da609e4693))


### Documentation

* add ui telephony integration ([3d710ca](https://github.com/dograh-hq/dograh/commit/3d710cafb12e718fd6ae654e95d12fb195d4a4fa))
* update dograh overview ([#63](https://github.com/dograh-hq/dograh/issues/63)) ([93a6a0a](https://github.com/dograh-hq/dograh/commit/93a6a0aef90ebb34bd9170b0e15c882ab1fd4ef4))

## [1.5.0](https://github.com/dograh-hq/dograh/compare/dograh-v1.4.0...dograh-v1.5.0) (2025-11-21)


### Features

* enable remote server deployment for OSS deployment ([#57](https://github.com/dograh-hq/dograh/issues/57)) ([6efe7d6](https://github.com/dograh-hq/dograh/commit/6efe7d6bd47e8cd099665351c7c8c6de645b1539))

## [1.4.0](https://github.com/dograh-hq/dograh/compare/dograh-v1.3.1...dograh-v1.4.0) (2025-11-15)


### Features

* enable workflows to be embedded in websites as a script tag ([#47](https://github.com/dograh-hq/dograh/issues/47)) ([99a768f](https://github.com/dograh-hq/dograh/commit/99a768f291b6a77bd0a32f6e4ffcc36f38c05dd4))
* simplify pipecat engine execution ([#54](https://github.com/dograh-hq/dograh/issues/54)) ([6ce25a5](https://github.com/dograh-hq/dograh/commit/6ce25a589ce84229b71bdc7208cf99ed35b35e5d))


### Documentation

* add development workflow to CONTRIBUTING.md ([#52](https://github.com/dograh-hq/dograh/issues/52)) ([6d7b0a9](https://github.com/dograh-hq/dograh/commit/6d7b0a951a68bf93df875b1d4f1e90c7b6df0317))
* update Slack link in README.md ([5e4aef3](https://github.com/dograh-hq/dograh/commit/5e4aef346d804dad25732a2aa07a7438a9d4fda0))

## [1.3.1](https://github.com/dograh-hq/dograh/compare/dograh-v1.3.0...dograh-v1.3.1) (2025-11-13)


### Bug Fixes

* upgrade pipecat with bundled Silero VAD model ([#50](https://github.com/dograh-hq/dograh/issues/50)) ([1e32eba](https://github.com/dograh-hq/dograh/commit/1e32ebae2a1334d1844c1b5618a60ed3b752d0b2))

## [1.3.0](https://github.com/dograh-hq/dograh/compare/dograh-v1.2.0...dograh-v1.3.0) (2025-11-12)


### Features

* improve workflow builder UX ([#41](https://github.com/dograh-hq/dograh/issues/41)) ([1a0a18a](https://github.com/dograh-hq/dograh/commit/1a0a18a435279f57ad1095c27c205913a504fa8a))


### Bug Fixes

* arm docker build step ([#48](https://github.com/dograh-hq/dograh/issues/48)) ([c028c79](https://github.com/dograh-hq/dograh/commit/c028c79b4034d5f9a5e3abe00c5b74f58169bfda))
* fix npm build ([#43](https://github.com/dograh-hq/dograh/issues/43)) ([8d05c9f](https://github.com/dograh-hq/dograh/commit/8d05c9f8909dfacb1905aa383cf58989ee57020f))
* slack annoucement workflow ([#46](https://github.com/dograh-hq/dograh/issues/46)) ([dc6d696](https://github.com/dograh-hq/dograh/commit/dc6d696af61225b4782dfea14868b398a2aa649e))

## [1.2.0](https://github.com/dograh-hq/dograh/compare/dograh-v1.1.0...dograh-v1.2.0) (2025-11-06)


### Features

* add chatwoot integration ([#39](https://github.com/dograh-hq/dograh/issues/39)) ([5c1fe2c](https://github.com/dograh-hq/dograh/commit/5c1fe2c6afc38327eac83515e8d9cc92b769fee8))
* add csv upload functionality ([3babb5c](https://github.com/dograh-hq/dograh/commit/3babb5ced61e259f480f8c0fdf225c57953fddf6))
* add csv upload functionality for OSS ([#29](https://github.com/dograh-hq/dograh/issues/29)) ([3babb5c](https://github.com/dograh-hq/dograh/commit/3babb5ced61e259f480f8c0fdf225c57953fddf6))
* add gmail integration for searching and reply to emails ([#34](https://github.com/dograh-hq/dograh/issues/34)) ([6503d80](https://github.com/dograh-hq/dograh/commit/6503d806c5b300e7685d64fc0fde5f9061ab2411))
* add issue templates ([35c9ab7](https://github.com/dograh-hq/dograh/commit/35c9ab7b07896ebff739326a0f8408975877aa9e))
* add issue templates ([fe664cb](https://github.com/dograh-hq/dograh/commit/fe664cb3a69f239212dbd2000868ea54900dbff2))
* add more issue templates ([8c5e9b4](https://github.com/dograh-hq/dograh/commit/8c5e9b426390fb7128bf50f1b5bdc6be691549cf))
* add privacy policy and terms of service links ([a2d02d8](https://github.com/dograh-hq/dograh/commit/a2d02d8326d20eac181a2850c3c2cef53e537ee7))
* add README, LICENSE, CONTRIBUTING ([957cdcf](https://github.com/dograh-hq/dograh/commit/957cdcf3632d04d977eb1dd4926f7774252ee86f))
* add vonage telephony ([#35](https://github.com/dograh-hq/dograh/issues/35)) ([4cfdc3d](https://github.com/dograh-hq/dograh/commit/4cfdc3d420ac060ee32c82768ef864a1b4148126))
* create docker-image.yml, update README.md and docker-compose.yaml ([43c56d0](https://github.com/dograh-hq/dograh/commit/43c56d0b95e076d3f920724f139a64d576832d97))
* Enable Poshog and Sentry for OSS ([#23](https://github.com/dograh-hq/dograh/issues/23)) ([90f7aac](https://github.com/dograh-hq/dograh/commit/90f7aac8ad2ed5e771f3c20f4265d4e2e63bae6f))
* enable posthog and sentry for oss ([90f7aac](https://github.com/dograh-hq/dograh/commit/90f7aac8ad2ed5e771f3c20f4265d4e2e63bae6f))
* Enable telephony for OSS ([#21](https://github.com/dograh-hq/dograh/issues/21)) ([8e2e5c9](https://github.com/dograh-hq/dograh/commit/8e2e5c9327655b3c2f34d4fc2708d7ac2a7885f6))
* multi stage dockerfile ([548e6f8](https://github.com/dograh-hq/dograh/commit/548e6f885b159aaa626939942994359a22c663d6))
* set start metadata in pipeline ([8376e3e](https://github.com/dograh-hq/dograh/commit/8376e3e949fc72b682d9b696a6ff640be79d51f9))
* Trickle ice candidates for faster WebRTC connection ([034c551](https://github.com/dograh-hq/dograh/commit/034c551931121ce1689f95dbee42aa4f3763cc49))
* Trickle ice candidates for faster WebRTC connection ([895af47](https://github.com/dograh-hq/dograh/commit/895af4748240fcd8cc2e14b711687d6a821eff2b))
* update readme and docker compose file ([606398b](https://github.com/dograh-hq/dograh/commit/606398b42742e30e2dd347941adeab304c7e5d23))
* UX improvements for onboarding ([d39a811](https://github.com/dograh-hq/dograh/commit/d39a8111a6f364e0ca89f1b6a06459db5134a5c7))


### Bug Fixes

* add minio policy ([136f370](https://github.com/dograh-hq/dograh/commit/136f370ea218983cf65c3e45f789f6877824d115))
* fix audio permission issue on safari ([#26](https://github.com/dograh-hq/dograh/issues/26)) ([e9c0afd](https://github.com/dograh-hq/dograh/commit/e9c0afd517bef7f4c4548c731e0422bd8b949610))
* fix ui of webrtc call ([efd93ad](https://github.com/dograh-hq/dograh/commit/efd93adfa87ebca70c12b291f6e82cc16f1e1596))
* install soundfile in oss docker build ([c7e7581](https://github.com/dograh-hq/dograh/commit/c7e75819f4c10705e700a6ef5cca568f7e3cadd2))
* install soundfile in oss docker build ([6a97fd1](https://github.com/dograh-hq/dograh/commit/6a97fd194e4d5bcca3e626e213d0d4b11f8ec5d4))
* link for 'Try Cloud Version' in README ([28926d0](https://github.com/dograh-hq/dograh/commit/28926d026f3e1514e3f749dfc68df902edbbd5cf))
* pipecat commit hash & add webrtc in requirements.txt ([d89bb84](https://github.com/dograh-hq/dograh/commit/d89bb84dd161011bd2d3d050a89db61e60aa6586))
* redirect user to /workflow page if they have workflow ([9cb7582](https://github.com/dograh-hq/dograh/commit/9cb75829cb055582b2c90aca3d61d2493b621b84))
* redirect user to /workflow page if they have workflow ([73664e6](https://github.com/dograh-hq/dograh/commit/73664e6268842af1793a089698ec6306cab7683a))
* release package name and add write permission for 'comment on release' step of deployment action ([#31](https://github.com/dograh-hq/dograh/issues/31)) ([b9d1720](https://github.com/dograh-hq/dograh/commit/b9d1720d94a5723836d5170de321e4602a59d6d7))
* renamed check_pipecat_sync.sh ([75af6cf](https://github.com/dograh-hq/dograh/commit/75af6cfa9c833693286ef5d99ce30a6a5ad1cf9c))
* rethrow NEXT_REDIRECT error ([4906393](https://github.com/dograh-hq/dograh/commit/490639309bb93ee9fce081adae753cb9ec8abc0b))
* telephony bugs and improve code structure ([#38](https://github.com/dograh-hq/dograh/issues/38)) ([d58f37f](https://github.com/dograh-hq/dograh/commit/d58f37ff42c19797771305cf6c59ec381ac90e44))


### Documentation

* add video for vonage config ([#40](https://github.com/dograh-hq/dograh/issues/40)) ([dca4904](https://github.com/dograh-hq/dograh/commit/dca4904e382151f3d6adb3aa94cef8390e74ac5e))


### Code Refactoring

* change pipecat to submodule & add github alerts ([a9a97ab](https://github.com/dograh-hq/dograh/commit/a9a97abefb7fee3d909b0111fdb65ff8cec8a530))
* change pipecat to submodule & add github alerts ([6562963](https://github.com/dograh-hq/dograh/commit/6562963018c613c5439c1253374cef83e088d15d))

## [1.1.0](https://github.com/dograh-hq/dograh/compare/dograh-copy-v1.0.0...dograh-copy-v1.1.0) (2025-10-09)


### Features

* add csv upload functionality ([3babb5c](https://github.com/dograh-hq/dograh/commit/3babb5ced61e259f480f8c0fdf225c57953fddf6))
* add csv upload functionality for OSS ([#29](https://github.com/dograh-hq/dograh/issues/29)) ([3babb5c](https://github.com/dograh-hq/dograh/commit/3babb5ced61e259f480f8c0fdf225c57953fddf6))
* add issue templates ([35c9ab7](https://github.com/dograh-hq/dograh/commit/35c9ab7b07896ebff739326a0f8408975877aa9e))
* add issue templates ([fe664cb](https://github.com/dograh-hq/dograh/commit/fe664cb3a69f239212dbd2000868ea54900dbff2))
* add more issue templates ([8c5e9b4](https://github.com/dograh-hq/dograh/commit/8c5e9b426390fb7128bf50f1b5bdc6be691549cf))
* add privacy policy and terms of service links ([a2d02d8](https://github.com/dograh-hq/dograh/commit/a2d02d8326d20eac181a2850c3c2cef53e537ee7))
* add README, LICENSE, CONTRIBUTING ([957cdcf](https://github.com/dograh-hq/dograh/commit/957cdcf3632d04d977eb1dd4926f7774252ee86f))
* create docker-image.yml, update README.md and docker-compose.yaml ([43c56d0](https://github.com/dograh-hq/dograh/commit/43c56d0b95e076d3f920724f139a64d576832d97))
* Enable Poshog and Sentry for OSS ([#23](https://github.com/dograh-hq/dograh/issues/23)) ([90f7aac](https://github.com/dograh-hq/dograh/commit/90f7aac8ad2ed5e771f3c20f4265d4e2e63bae6f))
* enable posthog and sentry for oss ([90f7aac](https://github.com/dograh-hq/dograh/commit/90f7aac8ad2ed5e771f3c20f4265d4e2e63bae6f))
* Enable telephony for OSS ([#21](https://github.com/dograh-hq/dograh/issues/21)) ([8e2e5c9](https://github.com/dograh-hq/dograh/commit/8e2e5c9327655b3c2f34d4fc2708d7ac2a7885f6))
* multi stage dockerfile ([548e6f8](https://github.com/dograh-hq/dograh/commit/548e6f885b159aaa626939942994359a22c663d6))
* set start metadata in pipeline ([8376e3e](https://github.com/dograh-hq/dograh/commit/8376e3e949fc72b682d9b696a6ff640be79d51f9))
* Trickle ice candidates for faster WebRTC connection ([034c551](https://github.com/dograh-hq/dograh/commit/034c551931121ce1689f95dbee42aa4f3763cc49))
* Trickle ice candidates for faster WebRTC connection ([895af47](https://github.com/dograh-hq/dograh/commit/895af4748240fcd8cc2e14b711687d6a821eff2b))
* update readme and docker compose file ([606398b](https://github.com/dograh-hq/dograh/commit/606398b42742e30e2dd347941adeab304c7e5d23))
* UX improvements for onboarding ([d39a811](https://github.com/dograh-hq/dograh/commit/d39a8111a6f364e0ca89f1b6a06459db5134a5c7))


### Bug Fixes

* add minio policy ([136f370](https://github.com/dograh-hq/dograh/commit/136f370ea218983cf65c3e45f789f6877824d115))
* fix audio permission issue on safari ([#26](https://github.com/dograh-hq/dograh/issues/26)) ([e9c0afd](https://github.com/dograh-hq/dograh/commit/e9c0afd517bef7f4c4548c731e0422bd8b949610))
* fix ui of webrtc call ([efd93ad](https://github.com/dograh-hq/dograh/commit/efd93adfa87ebca70c12b291f6e82cc16f1e1596))
* install soundfile in oss docker build ([c7e7581](https://github.com/dograh-hq/dograh/commit/c7e75819f4c10705e700a6ef5cca568f7e3cadd2))
* install soundfile in oss docker build ([6a97fd1](https://github.com/dograh-hq/dograh/commit/6a97fd194e4d5bcca3e626e213d0d4b11f8ec5d4))
* link for 'Try Cloud Version' in README ([28926d0](https://github.com/dograh-hq/dograh/commit/28926d026f3e1514e3f749dfc68df902edbbd5cf))
* pipecat commit hash & add webrtc in requirements.txt ([d89bb84](https://github.com/dograh-hq/dograh/commit/d89bb84dd161011bd2d3d050a89db61e60aa6586))
* redirect user to /workflow page if they have workflow ([9cb7582](https://github.com/dograh-hq/dograh/commit/9cb75829cb055582b2c90aca3d61d2493b621b84))
* redirect user to /workflow page if they have workflow ([73664e6](https://github.com/dograh-hq/dograh/commit/73664e6268842af1793a089698ec6306cab7683a))
* renamed check_pipecat_sync.sh ([75af6cf](https://github.com/dograh-hq/dograh/commit/75af6cfa9c833693286ef5d99ce30a6a5ad1cf9c))
* rethrow NEXT_REDIRECT error ([4906393](https://github.com/dograh-hq/dograh/commit/490639309bb93ee9fce081adae753cb9ec8abc0b))


### Code Refactoring

* change pipecat to submodule & add github alerts ([a9a97ab](https://github.com/dograh-hq/dograh/commit/a9a97abefb7fee3d909b0111fdb65ff8cec8a530))
* change pipecat to submodule & add github alerts ([6562963](https://github.com/dograh-hq/dograh/commit/6562963018c613c5439c1253374cef83e088d15d))
