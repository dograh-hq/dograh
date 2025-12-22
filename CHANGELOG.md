# Changelog

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
