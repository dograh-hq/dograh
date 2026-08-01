# Dograh Git Workflow (Fork-and-Pull)

This document outlines the gold standard Git workflow for contributing to the Dograh repository, using the Fork-and-Pull model.

## Why this is the best approach:
1. **Pristine Local `main` Branch**: By never committing directly to your local `main` branch, you guarantee it will never drift or get into messy conflict states with `upstream/main`. Your local `main` essentially just acts as a clean mirror of the official Dograh repository. *(Note: Your local `main` is also what is deployed to your server, so keeping it clean and stable is critical!)*
2. **Feature Isolation**: By creating a new branch (like `wait-tool`) off your clean `main` and pushing it to your personal fork (`arnofrxdd`), you isolate your work. If you make a mistake, you can always just delete the branch and start over from your clean `main` without losing any official repo code.
3. **Painless Updates**: When you pull updates from `upstream/main` every 1-3 days, you never have to deal with massive merge conflicts. Your local `main` fast-forwards instantly, and any new branches you create are built on top of the latest, most stable code.

## The Ideal Development Cycle:

1. **Go to your clean local mirror:**
   ```bash
   git checkout main
   ```

2. **Sync your local main with Dograh's latest code:**
   ```bash
   git pull upstream main
   ```

3. **Create an isolated space for your new feature/fix:**
   ```bash
   git checkout -b new-feature
   ```

4. **Write code...** (Make your commits here)

5. **Push to your fork and open a PR:**
   ```bash
   git push arnofrxdd new-feature
   ```

6. **Go back to safety and repeat!**
   ```bash
   git checkout main
   ```
