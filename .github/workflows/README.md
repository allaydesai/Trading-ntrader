# GitHub Actions Configuration

This directory contains GitHub Actions workflows for automated CI/CD and code review.

## Claude PR Review Action

The `claude-pr-review.yml` workflow automatically reviews Pull Requests using Claude AI when:
- A PR is opened, synchronized (new commits), or reopened
- The PR targets `main` or `develop` branches
- The PR contains changes to reviewable files (`.py`, `.md`, `.yml`, `.yaml`, `.toml`, `.json`)

### Setup Requirements

1. **Anthropic API Key**: Add your Anthropic API key as a repository secret named `ANTHROPIC_API_KEY`
   - Go to your repository → Settings → Secrets and variables → Actions
   - Click "New repository secret"
   - Name: `ANTHROPIC_API_KEY`
   - Value: Your Anthropic API key

2. **GitHub Token**: The workflow uses `GITHUB_TOKEN` which is automatically provided by GitHub Actions

### Features

- **Intelligent Review**: Claude analyzes code changes with context of the Nautilus Trader framework
- **Trading Domain Expertise**: Specialized review for financial/trading system code
- **Code Quality Focus**: Reviews for type hints, testing, architecture, and best practices
- **Constructive Feedback**: Provides specific, actionable suggestions
- **Automatic Posting**: Posts review as a PR comment
- **Error Handling**: Graceful error handling with informative messages

### Customization

#### Skip Review for Specific PRs
Add the label `skip-claude-review` to a PR to skip the automated review.

#### Modify Review Focus
Edit `.github/scripts/claude_reviewer.py` to adjust:
- Review criteria and focus areas
- Claude model (currently using `claude-3-sonnet-20240229`)
- Token limits and content processing
- Review formatting and style

#### Add More File Types
Modify the `files` section in the workflow to include additional file extensions:

```yaml
files: |
  **/*.py
  **/*.md
  **/*.yml
  **/*.yaml
  **/*.toml
  **/*.json
  **/*.js    # Add JavaScript
  **/*.ts    # Add TypeScript
```

### Review Process

1. **Trigger**: PR opened/updated with relevant file changes
2. **Analysis**: Claude analyzes the diff and file contents
3. **Context**: Reviews with knowledge of:
   - Nautilus Trader framework best practices
   - Python trading system patterns
   - TDD and testing requirements
   - Code quality standards
4. **Feedback**: Posts structured review with:
   - Overall assessment
   - File-by-file feedback
   - Critical issues identification
   - Improvement suggestions
   - Recognition of good practices

### Troubleshooting

**Review not triggering:**
- Check if PR targets `main` or `develop` branch
- Verify changed files include reviewable extensions
- Ensure PR doesn't have `skip-claude-review` label

**API errors:**
- Verify `ANTHROPIC_API_KEY` is correctly set in repository secrets
- Check GitHub Action logs for detailed error messages
- Ensure API key has sufficient credits/quota

**Review quality issues:**
- Review content is limited by API token constraints
- Very large PRs may have truncated analysis
- Consider breaking large changes into smaller PRs

### Security Considerations

- API keys are stored as encrypted repository secrets
- The workflow has minimal permissions (read contents, write PR comments)
- No sensitive code or data is logged in GitHub Actions
- Claude API calls are made over HTTPS with API key authentication

### Cost Management

- Reviews only trigger on relevant file changes
- Content is truncated to manage API token usage
- Limited to 10 files per review to control costs
- Consider setting up usage monitoring in your Anthropic account
