# Contributing to Dograh AI

Welcome to Dograh AI! ❤️ Thank you for your interest in contributing to the future of open-source voice AI. ❤️

Dograh AI is a comprehensive voice agent platform that helps developers build, test, and deploy conversational AI systems with minimal setup. This guide will help you understand the project structure, set up your development environment, and start contributing effectively.

👉 Join our community → [Dograh Community Slack](https://join.slack.com/t/dograh-community/shared_invite/zt-4anp45mlu-ukR3jTqwdDFMEpqeBhKn6g)

## 🏗️ Project Overview

### What is Dograh AI?

Dograh AI is a full-stack platform for building voice agents with a drag-and-drop workflow builder. It combines multiple technologies to provide a seamless experience from development to production deployment.

## 🙌 How You Can Contribute

- 🐛 **Report bugs** via [GitHub Issues](https://github.com/dograh-hq/dograh/issues)
- 💡 **Suggest features** via [Ideas](https://github.com/orgs/dograh-hq/discussions/categories/ideas)
- 🔧 **Submit pull requests**
- 📖 **Improve documentation** The documentation is hosted via mintlify and the code is in `docs/` folder
- 💬 **Join the Slack community**

👉 A great place to start is with issues tagged **`good first issue`**.

> And if you like the project, but just don't have time to contribute code, that's fine. There are other easy ways to support the project:
>
> - Star the project;
> - Tweet about it;
> - Refer to this project in your project's readme;
> - Submit and vote on [Ideas](https://github.com/orgs/dograh-hq/discussions/categories/ideas);
> - Create and comment on [Issues](https://github.com/dograh-hq/dograh/issues);
> - Mention the project at local meetups and tell your friends/colleagues.

## 🚀 Development Setup

Please refer to our [Development Setup documentation](https://docs.dograh.com/contribution/setup).

### Getting Help

**Before You Start**

- Check existing [GitHub Issues](../../issues) for similar work
- Join our [Slack community](https://join.slack.com/t/dograh-community/shared_invite/zt-4anp45mlu-ukR3jTqwdDFMEpqeBhKn6g) to discuss your plans
- Look for issues tagged `good first issue` for beginner-friendly tasks

**During Development**

- Ask questions in our Slack community
- Reference related issues and PRs in your discussions
- Share early drafts for feedback on complex features

## ✍️ Contributor License Agreement

Before we can merge your first pull request, you need to sign the [Dograh Contributor License Agreement](CLA.md) (CLA). You keep the copyright to your contribution. The CLA grants Zansat Technologies Private Limited, the company behind Dograh, a license to use and distribute your contribution, including a patent license, and confirms that you have the right to contribute it. It is adapted from the Apache Software Foundation's Individual CLA, the template most company-backed open source projects base their CLA on.

Signing takes one comment:

1. Open your pull request. If you haven't signed yet, the CLA check comments with instructions.
2. Reply on the pull request with exactly: `I have read the CLA Document and I hereby sign the CLA`
3. The `license/cla` check turns green. You only sign once, and it covers all your future pull requests.

The check looks at the author of every commit in the pull request, so every author must sign, and each commit's author email must be linked to a GitHub account. `Co-authored-by` trailers are not checked. Comment `recheck` to run the check again.

If your employer has rights to the code you write, make sure you have their permission to contribute it, or ask them to sign the [Corporate CLA](CORPORATE_CLA.md) and email it to [contact@dograh.com](mailto:contact@dograh.com). You still sign the individual CLA on your pull request.

## Pull Request Requirements

### Telephony Provider Integration Pull Requests

Telephony changes require thorough review and testing. Every telephony pull request must follow the requirements in this section and include clear documentation and a video demonstrating the complete integration and end-to-end local testing. Maintainers will use these requirements when evaluating whether a pull request is ready for review.

#### Required Evidence

The video must demonstrate all of the following:

- All provider-side setup required before configuring the integration in Dograh, including where to find the account credentials and any other required values
- Configuring the provider integration in Dograh
- Outbound calls
- Inbound calls
- Number provisioning and any required KYC flow
- Error handling, including an attempt to add a number that the provider account does not own

The pull request must also document the provider setup, configuration, API behavior, number-provisioning flow, and KYC requirements. Where the implementation relies on a specific provider API, add a link to the relevant provider API documentation in a code comment near the applicable logic.

#### Scope of Telephony Integrations

A telephony provider integration pull request must focus on complete, working core calling functionality. Ideally, the integration should support both inbound and outbound calls. If the provider does not support one direction, or it cannot reasonably be included, explain the limitation and its effect on the integration in the pull request.

Additional capabilities, such as call transfer or other provider-specific add-ons, must be submitted in separate pull requests. Keeping these features separate allows maintainers to validate the core integration independently.

Pull requests that omit required documentation, have API mismatches, leave number provisioning or KYC unclear, or do not adequately demonstrate the core calling functionality may be blocked or rejected, depending on the size of the gaps and the pull request's overall compliance with this guide.

### AI Provider Integration Pull Requests

This section applies to new or changed TTS, STT, LLM, realtime, embeddings, and other third-party AI providers.

#### Provider Eligibility

Before maintainers perform detailed code review, the pull request must explain why Dograh should support the provider: the user need or maintainer sponsorship, the clear benefit over providers already supported, and links to the provider's public API documentation and pricing. The provider must have a usable public API, self-service account or credential setup, and a credible support or maintenance path.

Providers must be generally available for production use, with a publicly documented and stable API, for at least six months. Alpha, beta, private-preview, or newly launched providers are not accepted by default. A maintainer may approve a documented exception before implementation when there is a compelling user or product need.

#### Required Evidence

Contributors must create or use a real provider account and test the complete integration manually in Dograh. Unit, mock, and provider-SDK tests are required where appropriate, but they are not evidence that the Dograh integration works.

The pull request must include redacted evidence of all of the following:

- Provider-side account and credential setup (never commit or share secrets)
- Configuring and saving the provider in the Dograh UI or API
- Running a real Dograh workflow through the same adapter, endpoint, protocol, and authentication scheme that the PR adds
- The resulting provider output and the selected settings
- Redacted provider API request/response logs showing the endpoint, protocol, status, and request fields (never include credentials or user data)
- Invalid-credential and network/error behaviour

For TTS, show real audio produced by Dograh and its voice, language, speed, format, sample rate, and duration as applicable. For STT, show a known audio input and transcript. For LLM and realtime providers, show a real Dograh turn and any claimed tool or structured-output behaviour.

Include the test date, Dograh commit SHA, provider endpoint/API version, and the command, workflow, or recording used to produce the evidence. A direct API request, provider sample SDK, or smoke test using a different protocol does not satisfy this requirement.

Pull requests without a convincing provider-value case or complete live Dograh evidence will be rejected without detailed implementation review.

### Bug-Fix Pull Requests

Before submitting a bug-fix pull request, search the [GitHub Issues](https://github.com/dograh-hq/dograh/issues) to determine whether the bug has already been reported. If no issue exists, create one that includes:

- The deployment mode where the bug occurs: the self-hosted or cloud-hosted application
- A clear description of the bug and its impact
- Steps to reproduce the problem
- Expected and actual behavior
- Screenshots, error messages, logs, or other supporting evidence, where applicable
- Environment and version details, along with any other information needed to investigate the issue

Link the existing or newly created issue in the bug-fix pull request. Use a [GitHub closing keyword](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/linking-a-pull-request-to-an-issue) when the pull request fully resolves the issue (for example, `Fixes #123`).

## 💬 Community & Support

Our Slack community is the heart of Dograh AI development:

- **Get Help**: Setup assistance and debugging support
- **Collaborate**: Discuss features and architectural decisions
- **Connect**: Meet other contributors and maintainers
- **Stay Updated**: Learn about contribution opportunities and releases

👉 **Join us**: [Dograh Community Slack](https://join.slack.com/t/dograh-community/shared_invite/zt-4anp45mlu-ukR3jTqwdDFMEpqeBhKn6g)

Thank you for helping us keep voice AI open and accessible! 🎉
