import os
import base64
import re
from github import Github
from typing import List, Dict, Any, Optional
import logging
from GithubClientBase import GithubClientBase

logger = logging.getLogger(__name__)

class GithubClientDirect(GithubClientBase):
    """Client that uses PyGithub to interact with repositories directly."""
    
    def __init__(self, github_token=None, use_cache=True):
        """
        Initialize client with GitHub token.
        
        Args:
            github_token: GitHub access token (if None, attempts to read from environment)
            use_cache: Whether to use caching to reduce API calls
        """
        super().__init__(use_cache=use_cache)
        
        token = github_token or os.getenv('GITHUB_TOKEN')
        if not token:
            raise ValueError("GitHub token is required. Set it in .env file or pass directly.")
        
        self.github = Github(token)
    
    def _list_repository_files(self, owner: str, repo: str, path: str = "", branch: str = None) -> List[Dict[str, Any]]:
        """
        List files in a repository path.
        
        Args:
            owner: Repository owner
            repo: Repository name
            path: Path in the repository
            branch: Branch to use
            
        Returns:
            List of file information dictionaries
        """
        repository = self.github.get_repo(f"{owner}/{repo}")
        logger.debug(f"Getting contents from repository {repository.name}, path: {path}")

        contents = repository.get_contents(path, ref=branch)
        # Handle both single file and directory cases
        if not isinstance(contents, list):
            contents = [contents]
            
        result = []
        for content in contents:
            item = {
                "name": content.name,
                "path": content.path,
                "type": "file" if content.type == "file" else "dir",
                "size": content.size
            }
            result.append(item)
        return result
    
    def _get_file_content(self, owner: str, repo: str, path: str, branch: str = None) -> str:
        """
        Get the content of a file.
        
        Args:
            owner: Repository owner
            repo: Repository name
            path: Path to the file
            branch: Branch to use
            
        Returns:
            Content of the file as string
        """
        repository = self.github.get_repo(f"{owner}/{repo}")
        content = repository.get_contents(path, ref=branch)
        
        if content.encoding == "base64":
            return base64.b64decode(content.content).decode('utf-8')
        return content.content
    
    def _get_default_branch(self, owner: str, repo: str) -> str:
        """
        Get the default branch for a repository.
        
        Args:
            owner: Repository owner
            repo: Repository name
            
        Returns:
            Name of the default branch
        """
        repository = self.github.get_repo(f"{owner}/{repo}")
        default_branch = repository.default_branch
        logger.info(f"Using default branch: {default_branch}")
        return default_branch
    
    def get_repository_stats(self, owner: str, repo: str) -> Dict[str, Any]:
        """
        Get repository statistics and metadata.
        
        Args:
            owner: Repository owner
            repo: Repository name
            
        Returns:
            Dictionary with repository statistics
        """
        repository = self.github.get_repo(f"{owner}/{repo}")
        
        stats = {
            "name": repository.name,
            "full_name": repository.full_name,
            "description": repository.description,
            "default_branch": repository.default_branch,
            "language": repository.language,
            "stars": repository.stargazers_count,
            "forks": repository.forks_count,
            "open_issues": repository.open_issues_count,
            "created_at": repository.created_at.isoformat() if repository.created_at else None,
            "updated_at": repository.updated_at.isoformat() if repository.updated_at else None,
            "is_private": repository.private,
            "is_archived": repository.archived,
            "license": repository.license.name if repository.license else None
        }
        
        return stats