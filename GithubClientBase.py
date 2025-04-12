import os
import logging
import concurrent.futures
from typing import List, Dict, Any, Optional, Set
from abc import ABC, abstractmethod
from repo_cache import RepoCache

logger = logging.getLogger(__name__)

class GithubClientBase(ABC):
    """Base abstract class for GitHub clients to ensure consistent interface."""
    
    def __init__(self, use_cache=True):
        """
        Initialize base GitHub client.
        
        Args:
            use_cache: Whether to use caching to reduce API calls
        """
        self.use_cache = use_cache
        self.cache = RepoCache() if use_cache else None
    
    @abstractmethod
    def _list_repository_files(self, owner: str, repo: str, path: str = "", branch: str = None) -> List[Dict[str, Any]]:
        """
        List files in a repository path - abstract method to be implemented by subclasses.
        
        Args:
            owner: Repository owner
            repo: Repository name
            path: Path in the repository
            branch: Branch to use
            
        Returns:
            List of file information dictionaries
        """
        pass
    
    @abstractmethod
    def _get_file_content(self, owner: str, repo: str, path: str, branch: str = None) -> str:
        """
        Get the content of a file - abstract method to be implemented by subclasses.
        
        Args:
            owner: Repository owner
            repo: Repository name
            path: Path to the file
            branch: Branch to use
            
        Returns:
            Content of the file as string
        """
        pass
    
    def list_repository_files(self, owner: str, repo: str, path: str = "", branch: str = None) -> List[Dict[str, Any]]:
        """
        List files in a repository path - wrapper with common error handling.
        
        Args:
            owner: Repository owner
            repo: Repository name
            path: Path in the repository
            branch: Branch to use
            
        Returns:
            List of file information dictionaries
        """
        try:
            return self._list_repository_files(owner, repo, path, branch)
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Error listing contents at '{path}': {error_msg}")
            raise e
    
    def get_file_content(self, owner: str, repo: str, path: str, branch: str = None) -> str:
        """
        Get the content of a file - wrapper with common error handling.
        
        Args:
            owner: Repository owner
            repo: Repository name
            path: Path to the file
            branch: Branch to use
            
        Returns:
            Content of the file as string
        """
        try:
            return self._get_file_content(owner, repo, path, branch)
        except Exception as e:
            logger.error(f"Error getting content for file '{path}': {e}")
            raise
    
    def get_repository_structure(self, owner: str, repo: str, branch: str = None, 
                               ignore_dirs: List[str] = None, max_file_size: int = 500000,
                               include_patterns: List[str] = None,
                               extensions: List[str] = None, 
                               force_refresh: bool = False,
                               batch_size: int = 10,
                               max_workers: int = 5) -> Dict[str, str]:
        """
        Recursively get the structure of a repository with optimized batch processing.
        
        Args:
            owner: Repository owner
            repo: Repository name
            branch: Branch to analyze
            ignore_dirs: Directories to ignore
            max_file_size: Maximum file size to include
            include_patterns: Patterns to specifically include
            extensions: File extensions to include
            force_refresh: Whether to force a refresh of the cache
            batch_size: Number of files to process in each batch
            max_workers: Maximum number of concurrent workers for file fetching
            
        Returns:
            Dictionary mapping file paths to contents
        """
        # Check if we can use cached data
        if self.use_cache and not force_refresh:
            cached_files = self.cache.get_repo_files(owner, repo, branch)
            if cached_files:
                logger.info(f"Using cached repository structure for {owner}/{repo}")
                return cached_files

        # Determine the branch to use
        if branch is None:
            branch = self._get_default_branch(owner, repo)
            logger.info(f"Using default branch: {branch}")

        if ignore_dirs is None:
            ignore_dirs = ['.git', 'node_modules', '__pycache__', 'dist', 'build']
            
        # Set up defaults for binary file extensions to skip
        binary_extensions = [
            '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.pdf', '.zip', 
            '.gz', '.tar', '.class', '.exe', '.dll', '.so'
        ]
        
        # Helper function to determine if a file should be included
        def should_include_file(path: str) -> bool:
            # Check extension filter
            if extensions:
                if not any(path.endswith(ext) for ext in extensions):
                    # Check include patterns as override
                    if include_patterns and any(pattern in path for pattern in include_patterns):
                        return True
                    return False
                
            # Skip binary files
            if any(path.endswith(ext) for ext in binary_extensions):
                return False
                
            return True
        
        # First, collect all file paths recursively
        all_file_paths = []
        visited_dirs = set()
        
        def collect_file_paths(path: str = ""):
            if path in visited_dirs:
                return
                
            visited_dirs.add(path)
            
            try:
                items = self.list_repository_files(owner, repo, path, branch)
                
                for item in items:
                    item_path = item.get("path", "")
                    item_type = item.get("type", "")
                    item_size = item.get("size", 0)
                    
                    # Skip ignored directories and their children
                    if any(ignored_dir in item_path for ignored_dir in ignore_dirs):
                        logger.debug(f"Skipping ignored directory: {item_path}")
                        continue
                    
                    if item_type == "dir":
                        # Queue directory for traversal
                        collect_file_paths(item_path)
                        
                    elif item_type == "file":
                        # Apply filters
                        if item_size > max_file_size:
                            logger.debug(f"Skipping large file: {item_path} ({item_size} bytes)")
                            continue
                            
                        if not should_include_file(item_path):
                            logger.debug(f"Skipping file based on filters: {item_path}")
                            continue
                        
                        # Add to list of files to fetch
                        all_file_paths.append(item_path)
            except Exception as e:
                logger.error(f"Error collecting files in {path}: {e}")
        
        # Start collection from root
        collect_file_paths()
        
        if not all_file_paths:
            logger.warning("No files found or all files were filtered out")
            return {}
            
        logger.info(f"Found {len(all_file_paths)} files to fetch")
        
        # Now fetch file contents in parallel batches
        result = {}
        
        # Process files in batches to avoid overwhelming the API
        for i in range(0, len(all_file_paths), batch_size):
            batch = all_file_paths[i:i+batch_size]
            logger.info(f"Processing batch {i//batch_size + 1}/{(len(all_file_paths) + batch_size - 1)//batch_size} ({len(batch)} files)")
            
            # Use thread pool for concurrent fetching
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                # Create a dict mapping future to file path for easy lookup when results come in
                future_to_path = {
                    executor.submit(self.get_file_content, owner, repo, path, branch): path 
                    for path in batch
                }
                
                # Process results as they complete
                for future in concurrent.futures.as_completed(future_to_path):
                    path = future_to_path[future]
                    try:
                        content = future.result()
                        result[path] = content
                        logger.debug(f"Added file: {path}")
                    except Exception as e:
                        logger.error(f"Error getting content for {path}: {e}")
        
        # Cache the results if enabled
        if self.use_cache and result:
            self.cache.cache_repo_files(owner, repo, result, branch)
        
        return result
    
    def _get_default_branch(self, owner: str, repo: str) -> str:
        """
        Get the default branch for a repository.
        May be overridden by subclasses with more efficient implementations.
        
        Args:
            owner: Repository owner
            repo: Repository name
            
        Returns:
            Name of the default branch (usually 'main' or 'master')
        """
        # Default implementation returns 'main' as a fallback
        # Subclasses should override this with actual API calls
        logger.info("Using default branch name 'main' (no repository metadata available)")
        return "main"
        
    def has_code_search(self) -> bool:
        """
        Check if this client supports code search capabilities.
        
        Returns:
            True if code search is supported, False otherwise
        """
        return hasattr(self, 'search_references') and callable(getattr(self, 'search_references'))
        
    def get_repository_stats(self, owner: str, repo: str) -> Dict[str, Any]:
        """
        Get repository statistics and metadata.
        Should be implemented by subclasses for specific client types.
        
        Args:
            owner: Repository owner
            repo: Repository name
            
        Returns:
            Dictionary with repository statistics
        """
        # Base implementation returns empty dict
        # Subclasses should override this with actual implementation
        return {}