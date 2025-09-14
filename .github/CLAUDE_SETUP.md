# Claude AI Code Review Setup Guide

This repository is configured with automated code review using Claude AI. Here's how to set it up:

## Prerequisites

1. **Anthropic API Key**: You need an API key from Anthropic to use Claude AI
   - Sign up at [console.anthropic.com](https://console.anthropic.com)
   - Generate an API key in your account settings
   - Keep this key secure - never commit it to code

## GitHub Repository Setup

### Step 1: Add the API Key as a Secret

1. Go to your GitHub repository
2. Navigate to **Settings** → **Secrets and variables** → **Actions**
3. Click **"New repository secret"**
4. Name: `ANTHROPIC_API_KEY`
5. Value: Your Anthropic API key (starts with `sk-ant-`)
6. Click **"Add secret"**

### Step 2: Verify Setup

The next time you create or update a pull request, the GitHub Action will:

1. ✅ **Success**: Post an automated Claude AI code review
2. ❌ **Missing API Key**: Post a helpful setup message

## How It Works

- **Triggers**: Automatically runs on PR creation, updates, and reopening
- **Scope**: Reviews Python, Markdown, YAML, TOML, and JSON files
- **Focus**: Trading system architecture, code quality, testing, and best practices
- **Output**: Detailed review comments with suggestions and praise

## Troubleshooting

### Common Issues

1. **"ANTHROPIC_API_KEY not configured"**
   - Solution: Follow Step 1 above to add the secret

2. **"Permission denied" errors**
   - Solution: Ensure the GitHub token has proper permissions (handled automatically)

3. **Review not posting**
   - Check the Actions tab for detailed logs
   - Verify the API key is valid and has sufficient credits

### Disabling Reviews

To skip Claude review on a specific PR, add the label `skip-claude-review`.

## Security Notes

- API keys are stored securely as GitHub secrets
- Keys are never logged or exposed in the workflow
- All API calls are made over HTTPS
- No code or sensitive data is stored by Claude AI

---

*For technical issues with the Claude reviewer, check the `.github/workflows/claude-pr-review.yml` workflow file and the `.github/scripts/claude_reviewer.py` script.*