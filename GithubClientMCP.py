import os
import base64
import re
import json
import subprocess
import logging
import tempfile
from typing import List, Dict, Any, Optional, Set
from GithubClientBase import GithubClientBase

logger = logging.getLogger(__name__)

class GithubClientMCP(GithubClientBase):
    """Client that uses GitHub MCP server to interact with repositories."""
    
    def __init__(self, use_cache=True, claude_executable=None):
        """
        Initialize client with Claude CLI for MCP commands.
        
        Args:
            use_cache: Whether to use caching to reduce API calls
            claude_executable: Path to Claude executable for MCP commands
        """
        super().__init__(use_cache=use_cache)
        self.claude_executable = claude_executable or "claude"
    
    def call_mcp_tool(self, tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Call a GitHub MCP tool using Claude CLI.
        
        Args:
            tool_name: Name of the MCP tool to call
            params: Parameters for the tool
            
        Returns:
            Response from the tool
        """
        try:
            with tempfile.NamedTemporaryFile(suffix='.txt', mode='w+') as temp_file:
                prompt = f"""
                I need to use the GitHub MCP tool "{tool_name}" with the following parameters:
                ```json
                {json.dumps(params, indent=2)}
                ```
                
                Please execute this MCP tool and return only the raw JSON response without any additional text, explanation, or formatting.
                """
                
                temp_file.write(prompt)
                temp_file.flush()
                
                result = subprocess.run(
                    [self.claude_executable, "send", temp_file.name],
                    capture_output=True,
                    text=True,
                    timeout=180  # 3-minute timeout
                )
                
                if result.returncode != 0:
                    logger.error(f"Claude MCP call error: {result.stderr}")
                    raise Exception(f"Failed to call GitHub MCP tool {tool_name}")
                
                # Extract JSON from the response (might be surrounded by markdown code blocks)
                output = result.stdout
                json_match = re.search(r'```(?:json)?\n([\s\S]*?)\n```', output)
                if json_match:
                    json_str = json_match.group(1).strip()
                else:
                    # Try to find raw JSON if not in code blocks
                    json_str = output.strip()
                
                try:
                    return json.loads(json_str)
                except json.JSONDecodeError:
                    logger.error(f"Failed to parse JSON from response: {output}")
                    raise Exception(f"Invalid JSON response from GitHub MCP tool {tool_name}")
                
        except subprocess.TimeoutExpired:
            logger.error("Claude MCP call timed out")
            raise Exception(f"Timeout calling GitHub MCP tool {tool_name}")
        except Exception as e:
            logger.error(f"Error calling GitHub MCP tool {tool_name}: {e}")
            raise
    
    def _list_repository_files(self, owner: str, repo: str, path: str = "", branch: str = None) -> List[Dict[str, Any]]:
        """
        List files in a repository path using MCP.
        
        Args:
            owner: Repository owner
            repo: Repository name
            path: Path in the repository
            branch: Branch to use
            
        Returns:
            List of file information dictionaries
        """
        contents = self.call_mcp_tool("get_file_contents", {
            "owner": owner,
            "repo": repo,
            "path": path,
            "branch": branch
        })
        
        # Handle both single file and directory cases
        if not isinstance(contents, list):
            contents = [contents]
            
        result = []
        for content in contents:
            item = {
                "name": content.get("name", ""),
                "path": content.get("path", ""),
                "type": "file" if content.get("type") == "file" else "dir",
                "size": content.get("size", 0)
            }
            result.append(item)
        return result
    
    def _get_file_content(self, owner: str, repo: str, path: str, branch: str = None) -> str:
        """
        Get the content of a file using MCP.
        
        Args:
            owner: Repository owner
            repo: Repository name
            path: Path to the file
            branch: Branch to use
            
        Returns:
            Content of the file as string
        """
        content = self.call_mcp_tool("get_file_contents", {
            "owner": owner,
            "repo": repo,
            "path": path,
            "branch": branch
        })
        
        # The content is returned as a base64-encoded string
        if "content" in content and content.get("encoding") == "base64":
            return base64.b64decode(content["content"]).decode('utf-8')
        
        # If it's not encoded or we're dealing with an unexpected response format
        return content.get("content", "")
    
    def _get_default_branch(self, owner: str, repo: str) -> str:
        """
        Get the default branch for a repository.
        
        Args:
            owner: Repository owner
            repo: Repository name
            
        Returns:
            Name of the default branch
        """
        try:
            repo_info = self.call_mcp_tool("search_repositories", {
                "query": f"repo:{owner}/{repo}"
            })
            
            if repo_info.get("total_count", 0) > 0:
                items = repo_info.get("items", [])
                if items and len(items) > 0:
                    branch = items[0].get("default_branch")
                    if branch:
                        logger.info(f"Using default branch: {branch}")
                        return branch
        except Exception as e:
            logger.error(f"Error getting default branch: {e}")
        
        # Fallback to 'main' if we couldn't determine the default branch
        logger.info("Could not determine default branch, using 'main'")
        return "main"
    
    def search_code(self, owner: str, repo: str, query: str, max_results: int = 100) -> List[Dict[str, Any]]:
        """
        Search for code in a repository using the GitHub code search API.
        
        Args:
            owner: Repository owner
            repo: Repository name
            query: Search query string
            max_results: Maximum number of results to return
            
        Returns:
            List of search result items
        """
        try:
            # Add repo: prefix if not already present
            if f"repo:{owner}/{repo}" not in query:
                query = f"repo:{owner}/{repo} {query}"
                
            # Split results into multiple pages if needed
            results = []
            page = 1
            per_page = min(100, max_results)  # Max 100 per page allowed by GitHub
            
            while len(results) < max_results:
                response = self.call_mcp_tool("search_code", {
                    "q": query,
                    "page": page,
                    "per_page": per_page
                })
                
                items = response.get("items", [])
                if not items:
                    break
                    
                results.extend(items)
                
                # Check if we've reached the end of results
                total_count = response.get("total_count", 0)
                if len(results) >= total_count or len(results) >= max_results:
                    break
                    
                page += 1
            
            return results[:max_results]  # Ensure we don't exceed max_results
            
        except Exception as e:
            logger.error(f"Error searching code in {owner}/{repo}: {e}")
            return []
    
    def search_references(self, owner: str, repo: str, filepath: str) -> Set[str]:
        """
        Search for all files that reference a specific file.
        
        Args:
            owner: Repository owner
            repo: Repository name
            filepath: Path to the file to find references for
            
        Returns:
            Set of filepaths that reference the specified file
        """
        try:
            # Extract filename and extension
            filename = os.path.basename(filepath)
            name, ext = os.path.splitext(filename)
            
            # Create search queries based on file type
            queries = []
            
            # Search by exact filename
            queries.append(f"\"{filename}\"")
            
            # For Python files, search for imports
            if ext == '.py':
                module_name = name
                if name == '__init__':
                    # For __init__.py, search for the directory name
                    dir_name = os.path.basename(os.path.dirname(filepath))
                    if dir_name:
                        module_name = dir_name
                
                # Search for both import statements
                queries.append(f"\"import {module_name}\"")
                queries.append(f"\"from {module_name}\"")
            
            # Run searches and combine results
            references = set()
            for query in queries:
                search_results = self.search_code(owner, repo, query)
                
                for item in search_results:
                    # Skip the file itself
                    if item.get("path") == filepath:
                        continue
                    
                    references.add(item.get("path"))
            
            return references
            
        except Exception as e:
            logger.error(f"Error searching references for {filepath}: {e}")
            return set()
    
    def get_repository_stats(self, owner: str, repo: str) -> Dict[str, Any]:
        """
        Get repository statistics and metadata.
        
        Args:
            owner: Repository owner
            repo: Repository name
            
        Returns:
            Dictionary with repository statistics
        """
        try:
            repo_info = self.call_mcp_tool("search_repositories", {
                "query": f"repo:{owner}/{repo}"
            })
            
            if repo_info.get("total_count", 0) > 0:
                items = repo_info.get("items", [])
                if items and len(items) > 0:
                    repo_data = items[0]
                    
                    stats = {
                        "name": repo_data.get("name"),
                        "full_name": repo_data.get("full_name"),
                        "description": repo_data.get("description"),
                        "default_branch": repo_data.get("default_branch"),
                        "language": repo_data.get("language"),
                        "stars": repo_data.get("stargazers_count"),
                        "forks": repo_data.get("forks_count"),
                        "open_issues": repo_data.get("open_issues_count"),
                        "created_at": repo_data.get("created_at"),
                        "updated_at": repo_data.get("updated_at"),
                        "is_private": repo_data.get("private", False),
                        "is_archived": repo_data.get("archived", False),
                        "license": repo_data.get("license", {}).get("name") if repo_data.get("license") else None
                    }
                    
                    return stats
            
            logger.warning(f"Could not get repository stats for {owner}/{repo}")
            return {}
            
        except Exception as e:
            logger.error(f"Error getting repository stats: {e}")
            return {}