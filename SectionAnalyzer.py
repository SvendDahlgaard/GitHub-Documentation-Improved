from pathlib import Path
from typing import List, Dict, Tuple, Set, Optional, Any
from collections import defaultdict
import re
import logging
import networkx as nx
from enum import Enum, auto
from repo_cache import RepoCache

logger = logging.getLogger(__name__)

class AnalysisMethod(Enum):
    """Enum for different section analysis methods."""
    STRUCTURAL = auto()  # Original directory-based method
    DEPENDENCY = auto()  # Dependency-based analysis
    HYBRID = auto()      # Combined approach

class SectionAnalyzer:
    """
    Analyzes a repository by sections using GitHub and Claude.
    
    This is a consolidated class that handles both basic and enhanced analysis
    based on the capabilities of the provided GitHub client.
    """
    
    def __init__(self, claude_analyzer=None, github_client=None, use_cache=True):
        """
        Initialize the section analyzer.
        
        Args:
            claude_analyzer: The Claude analyzer for code analysis
            github_client: The GitHub client for repository interactions
            use_cache: Whether to use caching for dependency information
        """
        self.claude_analyzer = claude_analyzer
        self.github_client = github_client
        self.use_cache = use_cache
        self.cache = RepoCache() if use_cache else None
        
        # Check if the GitHub client supports enhanced features
        self.has_code_search = False
        if github_client and hasattr(github_client, 'has_code_search'):
            self.has_code_search = github_client.has_code_search()
    
    def analyze_repository(self, repo_files: Dict[str, str], 
                         method: AnalysisMethod = AnalysisMethod.DEPENDENCY,
                         max_section_size: int = 15,
                         min_section_size: int = 2,
                         owner: str = None,
                         repo: str = None,
                         branch: str = None) -> List[Tuple[str, Dict[str, str]]]:
        """
        Analyze repository using the specified method.
        
        Args:
            repo_files: Dictionary mapping file paths to contents
            method: Analysis method to use
            max_section_size: Maximum number of files in a section before subdivision
            min_section_size: Minimum number of files in a section (smaller sections will be merged)
            owner: Repository owner (required for enhanced analysis)
            repo: Repository name (required for enhanced analysis)
            branch: Repository branch
            
        Returns:
            List of tuples (section_name, {file_path: content})
        """
        # Check if we can use cached structure
        if self.use_cache and owner and repo and method in [AnalysisMethod.DEPENDENCY, AnalysisMethod.HYBRID]:
            # Try to get cached structure
            cached_structure = self.cache.get_repo_structure(owner, repo, branch)
            if cached_structure:
                logger.info(f"Using cached structure for {owner}/{repo}")
                
                # Verify that it contains the same files we're analyzing now
                if set(repo_files.keys()) == set(cached_structure.get("files", [])):
                    cached_sections = cached_structure.get("sections", {})
                    if cached_sections:
                        # Convert cached section data to expected format
                        sections = []
                        for section_name, file_paths in cached_sections.items():
                            section_files = {path: repo_files[path] for path in file_paths if path in repo_files}
                            if section_files:
                                sections.append((section_name, section_files))
                        
                        if sections:
                            logger.info(f"Using {len(sections)} cached sections from repository structure")
                            return sections
                            
        # Select the appropriate analysis method based on capabilities and request
        if method == AnalysisMethod.DEPENDENCY:
            # Use enhanced dependency analysis if available
            if self.has_code_search and owner and repo:
                logger.info(f"Using enhanced dependency analysis for {owner}/{repo}")
                sections = self.enhanced_dependency_analysis(repo_files, owner, repo, max_section_size, branch)
            else:
                logger.info("Using basic dependency analysis (code search not available)")
                sections = self.dependency_analysis(repo_files, max_section_size)
                
        elif method == AnalysisMethod.HYBRID:
            # For hybrid, start with structural and refine with dependencies
            if self.has_code_search and owner and repo:
                logger.info(f"Using enhanced hybrid analysis for {owner}/{repo}")
                # Start with structural sections
                structural_sections = self.structural_analysis(repo_files, max_section_size)
                
                # Extract enhanced dependencies
                dependencies = self._extract_enhanced_dependencies(repo_files, owner, repo)
                
                # Refine large sections with dependency information
                sections = []
                for section_name, files in structural_sections:
                    if len(files) > max_section_size:
                        # Create subgraph for just this section
                        section_deps = {
                            src: {tgt for tgt in deps if tgt in files}
                            for src, deps in dependencies.items() if src in files
                        }
                        
                        # Get dependency-based subsections
                        subsections = self._group_by_dependencies(
                            files, section_deps, max_section_size
                        )
                        
                        # Rename subsections based on parent section
                        for i, (subsection_name, subsection_files) in enumerate(subsections):
                            new_name = f"{section_name}/{subsection_name}"
                            sections.append((new_name, subsection_files))
                    else:
                        sections.append((section_name, files))
            else:
                # Use regular hybrid analysis
                logger.info("Using basic hybrid analysis (code search not available)")
                sections = self.hybrid_analysis(repo_files, max_section_size)
        else:
            # Structural analysis is the same regardless of client capabilities
            sections = self.structural_analysis(repo_files, max_section_size)
        
        # Apply minimum section size if specified
        if min_section_size > 1:
            sections = self._merge_small_sections(sections, min_section_size)
        
        # Cache the sections if we have owner/repo info
        if self.use_cache and owner and repo:
            # Transform sections to a cacheable format
            cacheable_sections = {}
            for section_name, files in sections:
                cacheable_sections[section_name] = list(files.keys())
            
            # Create or update structure cache
            structure_data = self.cache.get_repo_structure(owner, repo, branch) or {}
            structure_data["sections"] = cacheable_sections
            structure_data["files"] = list(repo_files.keys())
            structure_data["section_method"] = method.name
            
            if "owner" not in structure_data:
                structure_data["owner"] = owner
                structure_data["repo"] = repo
                structure_data["branch"] = branch
            
            self.cache.update_structure_cache(owner, repo, repo_files, branch)
            
            # Update metadata with sections information
            metadata = self.cache.get_repo_metadata(owner, repo, branch) or {}
            metadata["section_count"] = len(sections)
            metadata["section_method"] = method.name
            metadata["sections"] = {name: len(files) for name, files in sections}
            self.cache.save_repo_metadata(owner, repo, metadata, branch)
            
        return sections