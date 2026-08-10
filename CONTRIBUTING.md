# Contributing to Dograh AI

Welcome to Dograh AI! ❤️ Thank you for your interest in contributing to the future of open-source voice AI. ❤️

Dograh AI is a comprehensive voice agent platform that helps developers build, test, and deploy conversational AI systems with minimal setup. This guide will help you understand the project structure, set up your development environment, and start contributing effectively.

👉 Join our community → [Dograh Community Slack](https://join.slack.com/t/dograh-community/shared_invite/zt-3zjb5vwvl-j7hRz3_F1SOn5cH~jm5f5g)

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
- Join our [Slack community](https://join.slack.com/t/dograh-community/shared_invite/zt-3zjb5vwvl-j7hRz3_F1SOn5cH~jm5f5g) to discuss your plans
- Look for issues tagged `good first issue` for beginner-friendly tasks

**During Development**

- Ask questions in our Slack community
- Reference related issues and PRs in your discussions
- Share early drafts for feedback on complex features

## Pull Request Requirements

### Telephony Pull Requests

Telephony changes require thorough review and testing. Every telephony pull request must follow the requirements in this section and include clear documentation and a video demonstrating the complete integration and end-to-end local testing. Maintainers will use these requirements when evaluating whether a pull request is ready for review.

The video must demonstrate all of the following:

- All provider-side setup required before configuring the integration in Dograh, including where to find the account credentials and any other required values
- Configuring the provider integration in Dograh
- Outbound calls
- Inbound calls
- Number provisioning and any required KYC flow
- Error handling, including an attempt to add a number that the provider account does not own

The pull request must also document the provider setup, configuration, API behavior, number-provisioning flow, and KYC requirements. Where the implementation relies on a specific provider API, add a link to the relevant provider API documentation in a code comment near the applicable logic.

#### Scope of Provider Integrations

A telephony provider integration pull request must focus on complete, working core calling functionality. Ideally, the integration should support both inbound and outbound calls. If the provider does not support one direction, or it cannot reasonably be included, explain the limitation and its effect on the integration in the pull request.

Additional capabilities, such as call transfer or other provider-specific add-ons, must be submitted in separate pull requests. Keeping these features separate allows maintainers to validate the core integration independently.

Pull requests that omit required documentation, have API mismatches, leave number provisioning or KYC unclear, or do not adequately demonstrate the core calling functionality may be blocked or rejected, depending on the size of the gaps and the pull request's overall compliance with this guide.

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

👉 **Join us**: [Dograh Community Slack](https://join.slack.com/t/dograh-community/shared_invite/zt-3zjb5vwvl-j7hRz3_F1SOn5cH~jm5f5g)

Thank you for helping us keep voice AI open and accessible! 🎉
