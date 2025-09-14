# Claude AI Code Review Setup Guide

This repository is configured with automated code review using Claude AI. Here's how to set it up:

## Prerequisites

### ⚠️ Important: API vs Subscription Difference
**Your Claude.ai subscription (Pro/Max) does NOT include API access.**

- **Claude.ai subscription**: For individual use through web/app interfaces
- **Anthropic API**: For developers building applications (required for GitHub Actions)

You need **both** if you want automated PR reviews plus personal Claude access.

### Getting API Access

1. **Create API Account**: Go to [console.anthropic.com](https://console.anthropic.com)
2. **Choose Build Plan**: Select "Build" for API access (not Scale)
3. **Add Payment**: Credit card required for pay-per-token usage
4. **Purchase Credits**: Buy initial credits ($5-20 recommended)
5. **Generate API Key**: Create key in console (starts with `sk-ant-`)

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

## Configuration Details

### Model Used
- **Current Model**: `claude-sonnet-4-20250514` (Claude Sonnet 4)
- **Capabilities**: Advanced reasoning, code analysis, architectural review
- **Pricing**: $3 per million input tokens, $15 per million output tokens
- **Context**: 200,000 tokens (handles large PRs effectively)

### Review Scope
- **File Types**: Python (.py), Markdown (.md), YAML (.yml/.yaml), TOML (.toml), JSON (.json)
- **Focus Areas**:
  - 🏗️ Architecture & Design (SOLID principles, clean code)
  - 🧪 Testing Strategy (TDD, coverage, pytest best practices)
  - 📊 Trading Logic (risk management, position sizing)
  - ⚡ Performance (async/await, optimization)
  - 🔒 Security (validation, no secrets)
  - 📝 Code Quality (type hints, docstrings)

### Cost Estimation
- **Typical PR Review**: $0.10 - $0.50 per review
- **Large PRs**: $0.50 - $2.00 per review
- **Monthly Cost**: ~$5-20 for moderate activity
- **Heavy Usage**: ~$20-50 for very active repositories

## How It Works

### Triggers
- **PR Creation**: New pull requests
- **PR Updates**: New commits pushed to PR
- **PR Reopening**: Closed PRs that are reopened
- **Branches**: Only on `main` and `develop` branches

### Review Process
1. **File Analysis**: Extracts changed files and content
2. **Context Building**: Combines diff with full file context
3. **Claude Analysis**: Senior-level review with trading expertise
4. **Structured Output**: Executive summary, file-by-file feedback, actionable suggestions

### Review Format
- 🎯 **Executive Summary**: Overall assessment and merge readiness
- 📁 **File-by-File Analysis**: Specific feedback with line references
- 🚨 **Critical Issues**: Security, performance, breaking changes
- 💡 **Improvement Suggestions**: Architecture and optimization ideas
- ✅ **What's Done Well**: Positive reinforcement

## Troubleshooting

### Common Issues

1. **"ANTHROPIC_API_KEY not configured"**
   - Solution: Follow Step 1 above to add the secret

2. **"Model not found" errors**
   - Usually indicates outdated model name
   - Current model: `claude-sonnet-4-20250514`

3. **"Insufficient credits" errors**
   - Add more credits in console.anthropic.com
   - Set up auto-reload to prevent interruptions

4. **Review not posting**
   - Check the Actions tab for detailed logs
   - Verify API key is valid and has credits

5. **"Permission denied" errors**
   - GitHub token permissions handled automatically
   - Check repository settings if issues persist

### Disabling Reviews

- **Single PR**: Add label `skip-claude-review`
- **Temporarily**: Disable the workflow in repository settings
- **Permanently**: Delete the workflow file

## Security Notes

- API keys stored securely as GitHub secrets
- Keys never logged or exposed in workflow output
- All API calls made over HTTPS
- No code or sensitive data stored by Claude AI
- API usage has separate data processing agreements with additional privacy protections

## Alternative Solutions

If you prefer not to pay for API access:

1. **Manual Reviews**: Use your Claude.ai subscription for manual PR reviews
2. **Free Tools**: GitHub's built-in code scanning, Codecov, etc.
3. **Open Source**: Consider free alternatives like automated linting only

---

*For technical issues, check `.github/workflows/claude-pr-review.yml` and `.github/scripts/claude_reviewer.py`*