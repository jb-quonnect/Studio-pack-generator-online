"""
Studio Pack Generator Online - Pack Builder

Orchestrates the complete pack generation process:
- Processes input files (folder structure, ZIP, or RSS)
- Converts audio/images to target format
- Generates missing navigation audio via TTS
- Builds story.json structure
- Creates final ZIP archive
"""

import os
import shutil
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any, Callable
from dataclasses import dataclass, field

from .utils import (
    clean_name, is_audio_file, is_image_file, is_special_file,
    ensure_dir, generate_uuid
)
from .session_manager import SessionState, get_session_manager
from .audio_processor import process_audio_to_asset, is_ffmpeg_available
from .image_processor import (
    process_image_to_asset, generate_text_image_to_asset,
    extract_image_from_mp3
)
from .story_generator import StoryGenerator, StageNode, ActionNode
from .tts_engine import get_tts_engine, synthesize_navigation_audio
from .zip_handler import create_pack_zip


logger = logging.getLogger(__name__)


@dataclass
class BuildOptions:
    """Options for pack building."""
    
    # Pack metadata
    title: str = "Mon Pack"
    description: str = ""
    
    # Audio processing
    normalize_audio: bool = True
    add_delay: bool = False
    
    # Navigation
    night_mode: bool = False
    auto_next_story: bool = False
    
    # TTS
    tts_model: str = "fr_FR-siwis-medium"
    generate_missing_audio: bool = True
    generate_missing_images: bool = True
    
    # Image generation font
    image_font: str = "Arial"
    
    # Progress callback
    progress_callback: Optional[Callable[[float, str], None]] = None


@dataclass
class TreeNode:
    """
    Represents a node in the input file tree.
    
    Can be a folder (menu) or a file (story).
    """
    name: str
    path: str
    is_folder: bool
    children: List['TreeNode'] = field(default_factory=list)
    
    # Associated files
    audio_file: Optional[str] = None  # For stories: the main audio
    item_audio: Optional[str] = None  # Navigation audio (0-item.mp3 or .item.mp3)
    item_image: Optional[str] = None  # Navigation image (0-item.png or .item.png)
    
    # Generated asset paths
    audio_asset: Optional[str] = None
    image_asset: Optional[str] = None
    nav_audio_asset: Optional[str] = None
    
    # Story node reference
    stage_node: Optional[StageNode] = None
    action_node: Optional[ActionNode] = None
    
    @property
    def display_name(self) -> str:
        """Get clean name for display/TTS."""
        return clean_name(self.name)


class PackBuilder:
    """
    Main pack builder class.
    
    Orchestrates the complete generation process.
    """
    
    def __init__(self, options: Optional[BuildOptions] = None):
        self.options = options or BuildOptions()
        self._session_manager = get_session_manager()
        self.session = self._session_manager.session
        self.tts = get_tts_engine()
        self.story_gen = StoryGenerator(
            title=self.options.title,
            description=self.options.description
        )
        
        # Track progress
        self._total_steps = 0
        self._current_step = 0
        
        # File counter for warning
        self._files_generated = 0
    
    def _update_progress(self, message: str):
        """Update progress callback if set."""
        if self.options.progress_callback:
            progress = self._current_step / max(self._total_steps, 1)
            self.options.progress_callback(progress, message)
        self._current_step += 1
    
    def _increment_file_count(self, count: int = 1):
        """Increment file counter and update session."""
        self._files_generated += count
        get_session_manager().increment_file_count(count)
    
    def build_from_tree(self, root: TreeNode) -> bool:
        """
        Build a pack from a parsed tree structure.
        
        Args:
            root: Root TreeNode representing the pack structure
            
        Returns:
            True if successful
        """
        logger.info(f"Building pack: {self.options.title}")
        
        # Count total steps for progress
        self._total_steps = self._count_nodes(root) * 3  # Process, generate, finalize
        self._current_step = 0
        
        # Step 1: Process all files (audio/image conversion)
        self._update_progress("Traitement des fichiers...")
        if not self._process_tree(root):
            return False
        
        # Step 2: Generate missing navigation assets
        self._update_progress("Génération des éléments de navigation...")
        if not self._generate_navigation(root):
            return False
        
        # Step 3: Build story.json structure
        self._update_progress("Construction de la structure...")
        if not self._build_structure(root):
            return False
        
        # Step 4: Set night mode if enabled
        if self.options.night_mode:
            self.story_gen.set_night_mode(True)
        
        # Step 5: Save story.json
        self._update_progress("Sauvegarde de story.json...")
        if not self.story_gen.save(self.session.output_dir):
            return False
        
        # Step 6: Create ZIP
        self._update_progress("Création du fichier ZIP...")
        zip_path = self._session_manager.get_output_zip_path()
        if not create_pack_zip(self.session.output_dir, zip_path):
            return False
        
        logger.info(f"Pack built successfully: {zip_path}")
        logger.info(f"Total files generated: {self._files_generated}")
        
        return True
    
    def _count_nodes(self, node: TreeNode) -> int:
        """Count total nodes in tree."""
        count = 1
        for child in node.children:
            count += self._count_nodes(child)
        return count
    
    def _process_tree(self, node: TreeNode, depth: int = 0) -> bool:
        """
        Process all files in the tree (conversion).
        
        Args:
            node: Current tree node
            depth: Current depth
            
        Returns:
            True if successful
        """
        if depth > 10:
            logger.warning(f"Max depth reached at: {node.path}")
            return True
        
        # Process this node's files
        if node.is_folder:
            # Process folder's item audio
            if node.item_audio and os.path.exists(node.item_audio):
                node.nav_audio_asset = process_audio_to_asset(
                    node.item_audio,
                    self.session.assets_dir,
                    normalize=self.options.normalize_audio,
                    add_delay=self.options.add_delay
                )
                if node.nav_audio_asset:
                    self._increment_file_count()
            
            # Process folder's item image
            if node.item_image and os.path.exists(node.item_image):
                node.image_asset = process_image_to_asset(
                    node.item_image,
                    self.session.assets_dir
                )
                if node.image_asset:
                    self._increment_file_count()
        
        else:
            # Process story audio
            if node.audio_file and os.path.exists(node.audio_file):
                node.audio_asset = process_audio_to_asset(
                    node.audio_file,
                    self.session.assets_dir,
                    normalize=self.options.normalize_audio,
                    add_delay=self.options.add_delay
                )
                if node.audio_asset:
                    self._increment_file_count()
            
            # Process story item audio (navigation)
            if node.item_audio and os.path.exists(node.item_audio):
                node.nav_audio_asset = process_audio_to_asset(
                    node.item_audio,
                    self.session.assets_dir,
                    normalize=self.options.normalize_audio
                )
                if node.nav_audio_asset:
                    self._increment_file_count()
            
            # Process story item image
            if node.item_image and os.path.exists(node.item_image):
                node.image_asset = process_image_to_asset(
                    node.item_image,
                    self.session.assets_dir
                )
                if node.image_asset:
                    self._increment_file_count()
            
            # Try to extract image from MP3 if no item image
            elif node.audio_file and not node.image_asset:
                temp_img = os.path.join(self.session.input_dir, "_temp_cover.png")
                if extract_image_from_mp3(node.audio_file, temp_img):
                    node.image_asset = process_image_to_asset(
                        temp_img,
                        self.session.assets_dir
                    )
                    if node.image_asset:
                        self._increment_file_count()
                    os.remove(temp_img)
        
        self._update_progress(f"Traitement: {node.display_name}")
        
        # Process children
        for child in node.children:
            if not self._process_tree(child, depth + 1):
                return False
        
        return True
    
    def _generate_navigation(self, node: TreeNode, depth: int = 0) -> bool:
        """
        Generate missing navigation audio/images.
        
        Args:
            node: Current tree node
            depth: Current depth
            
        Returns:
            True if successful
        """
        if depth > 10:
            return True
        
        display_name = node.display_name
        
        # Generate missing navigation audio
        if not node.nav_audio_asset and self.options.generate_missing_audio:
            temp_audio = os.path.join(self.session.input_dir, f"_tts_{generate_uuid()}.mp3")
            
            if synthesize_navigation_audio(display_name, temp_audio, self.options.tts_model):
                node.nav_audio_asset = process_audio_to_asset(
                    temp_audio,
                    self.session.assets_dir,
                    normalize=True
                )
                if node.nav_audio_asset:
                    self._increment_file_count()
                
                # Clean up temp file
                if os.path.exists(temp_audio):
                    os.remove(temp_audio)
        
        # Generate missing image
        if not node.image_asset and self.options.generate_missing_images:
            node.image_asset = generate_text_image_to_asset(
                display_name,
                self.session.assets_dir,
                font_name=self.options.image_font
            )
            if node.image_asset:
                self._increment_file_count()
        
        self._update_progress(f"Navigation: {display_name}")
        
        # Process children
        for child in node.children:
            if not self._generate_navigation(child, depth + 1):
                return False
        
        return True
    
    def _build_structure(self, root: TreeNode) -> bool:
        """
        Build the story.json structure from the tree.
        
        Creates the node graph:
          Entrypoint (cover) → "Choisis une histoire" (auto) → Story menu (wheel)
        
        Args:
            root: Root tree node
            
        Returns:
            True if successful
        """
        # Create entrypoint
        entrypoint = self.story_gen.create_entrypoint(
            name=root.display_name,
            image=f"assets/{root.image_asset}" if root.image_asset else None,
            audio=f"assets/{root.nav_audio_asset}" if root.nav_audio_asset else None
        )
        
        # If root has children, create the "Choisis une histoire" intermediate node
        if root.children:
            # Generate TTS for "Choisis une histoire"
            choose_audio_asset = None
            if self.options.generate_missing_audio:
                temp_audio = os.path.join(
                    self.session.input_dir, f"_tts_choose_{generate_uuid()}.mp3"
                )
                if synthesize_navigation_audio(
                    "Choisis une histoire", temp_audio, self.options.tts_model
                ):
                    from .audio_processor import process_audio_to_asset
                    choose_audio_asset = process_audio_to_asset(
                        temp_audio, self.session.assets_dir, normalize=True
                    )
                    if choose_audio_asset:
                        self._increment_file_count()
                    if os.path.exists(temp_audio):
                        os.remove(temp_audio)
            
            # Create "Choisis une histoire" intermediate node
            choose_node = self.story_gen.create_menu(
                name="Choisis une histoire",
                audio=f"assets/{choose_audio_asset}" if choose_audio_asset else None,
            )
            # Auto-play + auto-transition (no wheel/ok on this node)
            choose_node.control_settings = {
                'wheel': False, 'ok': False, 'home': True,
                'pause': False, 'autoplay': True
            }
            
            # Entrypoint → action1 (1 option: choose_node)
            action_entry = self.story_gen.create_action([choose_node.uuid])
            self.story_gen.link_node_to_action(entrypoint, action_entry)
            # optionIndex=0 → auto-jump to the single option (choose_node)
            entrypoint.ok_option_index = 0
            
            # Choose node → action2 (N options: story children) with optionIndex=-1 (wheel)
            action_menu = self.story_gen.create_action()
            self.story_gen.link_node_to_action(choose_node, action_menu)
            choose_node.ok_option_index = -1  # Wheel scroll mode for story selection
            
            # Process children into the menu action
            for child in root.children:
                self._build_node(child, action_menu)
        
        return True
    
    def _build_node(self, node: TreeNode, parent_action: ActionNode, depth: int = 0) -> None:
        """
        Build a single node in the story structure.
        
        Args:
            node: Tree node to build
            parent_action: Parent action node to link to
            depth: Current depth
        """
        if depth > 10:
            return
        
        if node.is_folder:
            # Create menu node
            menu = self.story_gen.create_menu(
                name=node.display_name,
                image=f"assets/{node.image_asset}" if node.image_asset else None,
                audio=f"assets/{node.nav_audio_asset}" if node.nav_audio_asset else None,
                parent_action_id=parent_action.id
            )
            
            # If has children, create action and process them
            if node.children:
                action = self.story_gen.create_action()
                self.story_gen.link_node_to_action(menu, action)
                
                for child in node.children:
                    self._build_node(child, action, depth + 1)
        
        else:
            # Create story node with navigation audio and story audio
            story = self.story_gen.create_story(
                name=node.display_name,
                audio=f"assets/{node.audio_asset}" if node.audio_asset else "",
                image=f"assets/{node.image_asset}" if node.image_asset else None,
                nav_audio=f"assets/{node.nav_audio_asset}" if node.nav_audio_asset else None,
                parent_action_id=parent_action.id
            )
    
    def get_output_zip_path(self) -> str:
        """Get path to the generated ZIP file."""
        return self._session_manager.get_output_zip_path()


def parse_folder_to_tree(folder_path: str) -> Optional[TreeNode]:
    """
    Parse a folder structure into a TreeNode hierarchy.
    
    Args:
        folder_path: Path to the root folder
        
    Returns:
        Root TreeNode or None if failed
    """
    if not os.path.isdir(folder_path):
        logger.error(f"Not a directory: {folder_path}")
        return None
    
    def parse_dir(path: str, name: str) -> TreeNode:
        """Recursively parse a directory."""
        node = TreeNode(
            name=name,
            path=path,
            is_folder=True
        )
        
        # Look for special files
        for special in ['0-item.mp3', '0-item.wav']:
            special_path = os.path.join(path, special)
            if os.path.exists(special_path):
                node.item_audio = special_path
                break
        
        for special in ['0-item.png', '0-item.jpg']:
            special_path = os.path.join(path, special)
            if os.path.exists(special_path):
                node.item_image = special_path
                break
        
        # Sort entries for consistent ordering
        entries = sorted(os.listdir(path))
        
        for entry in entries:
            entry_path = os.path.join(path, entry)
            
            # Skip special files
            if is_special_file(entry):
                continue
            
            if os.path.isdir(entry_path):
                # Recurse into subdirectory
                child = parse_dir(entry_path, entry)
                node.children.append(child)
            
            elif is_audio_file(entry):
                # Create story node
                child = TreeNode(
                    name=entry,
                    path=entry_path,
                    is_folder=False,
                    audio_file=entry_path
                )
                
                # Look for associated .item files
                base = os.path.splitext(entry)[0]
                for ext in ['.mp3', '.wav']:
                    item_audio = os.path.join(path, f"{base}.item{ext}")
                    if os.path.exists(item_audio):
                        child.item_audio = item_audio
                        break
                
                for ext in ['.png', '.jpg']:
                    item_image = os.path.join(path, f"{base}.item{ext}")
                    if os.path.exists(item_image):
                        child.item_image = item_image
                        break
                
                node.children.append(child)
        
        return node
    
    folder_name = os.path.basename(folder_path.rstrip('/\\'))
    return parse_dir(folder_path, folder_name)


def build_pack_from_folder(
    folder_path: str,
    options: Optional[BuildOptions] = None
) -> Optional[str]:
    """
    Convenience function to build a pack from a folder.
    
    Args:
        folder_path: Path to source folder
        options: Build options
        
    Returns:
        Path to generated ZIP or None if failed
    """
    tree = parse_folder_to_tree(folder_path)
    if not tree:
        return None
    
    builder = PackBuilder(options)
    if builder.build_from_tree(tree):
        return builder.get_output_zip_path()
    
    return None
