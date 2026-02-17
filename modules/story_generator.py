"""
Studio Pack Generator Online - Story JSON Generator

Generates the story.json file that defines the navigation structure
of a Studio Pack, including stageNodes and actionNodes.
"""

import json
import os
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field, asdict
from enum import Enum

from .utils import generate_uuid


logger = logging.getLogger(__name__)


class NodeType(Enum):
    """Types of stage nodes in a Studio Pack."""
    ENTRYPOINT = "entrypoint"
    MENU = "menu"
    STORY = "story"
    COVER = "cover"  # Cover/title screen


@dataclass
class StageNode:
    """
    Represents a stage (screen) in the story navigation.
    
    Can be an entrypoint, menu, or story node.
    """
    uuid: str
    type: str
    name: str
    image: Optional[str] = None  # Path in assets/
    audio: Optional[str] = None  # Navigation audio (announcement)
    story_audio: Optional[str] = None  # Story audio (for story nodes only)
    ok_transition: Optional[str] = None  # ActionNode ID for OK button
    ok_option_index: Optional[int] = None  # Option index (-1=wheel, >=0=auto-select)
    home_transition: Optional[str] = None  # ActionNode ID for home
    control_settings: Optional[Dict] = None
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization."""
        result = {
            "uuid": self.uuid,
            "type": self.type,
            "name": self.name,
        }
        
        if self.image:
            result["image"] = self.image
        if self.audio:
            result["audio"] = self.audio
        if self.story_audio:
            result["storyAudio"] = self.story_audio
        if self.ok_transition:
            ok_trans = {"actionNode": self.ok_transition}
            if self.ok_option_index is not None:
                ok_trans["optionIndex"] = self.ok_option_index
            result["okTransition"] = ok_trans
        if self.home_transition:
            result["homeTransition"] = {"actionNode": self.home_transition}
        if self.control_settings:
            result["controlSettings"] = self.control_settings
        
        return result


@dataclass
class ActionNode:
    """
    Represents a choice/action in the story navigation.
    
    Links multiple stage nodes as options the user can navigate between.
    """
    id: str
    options: List[str] = field(default_factory=list)  # List of StageNode UUIDs
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "options": self.options
        }


@dataclass
class StoryPack:
    """
    Complete Studio Pack structure.
    
    Contains metadata and the full navigation graph.
    """
    title: str = "Mon Pack"
    description: str = ""
    format: str = "v1"
    version: int = 2
    night_mode: bool = False
    
    stage_nodes: List[StageNode] = field(default_factory=list)
    action_nodes: List[ActionNode] = field(default_factory=list)
    
    # Root node reference
    entrypoint_uuid: Optional[str] = None
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "format": self.format,
            "version": self.version,
            "title": self.title,
            "description": self.description,
            "nightMode": self.night_mode,
            "stageNodes": [node.to_dict() for node in self.stage_nodes],
            "actionNodes": [node.to_dict() for node in self.action_nodes]
        }
    
    def to_json(self, indent: int = 2) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)
    
    def save(self, output_path: str) -> bool:
        """
        Save story.json to file.
        
        Args:
            output_path: Path to save the JSON file
            
        Returns:
            True if successful
        """
        try:
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(self.to_json())
            logger.info(f"Saved story.json to {output_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to save story.json: {e}")
            return False


class StoryGenerator:
    """
    Generates a StoryPack from a hierarchical node structure.
    
    Handles the creation of stageNodes and actionNodes to form
    a complete navigation graph.
    """
    
    def __init__(self, title: str = "Mon Pack", description: str = ""):
        self.pack = StoryPack(title=title, description=description)
        self._node_map: Dict[str, StageNode] = {}
        self._action_map: Dict[str, ActionNode] = {}
    
    def create_entrypoint(
        self,
        name: str,
        image: Optional[str] = None,
        audio: Optional[str] = None
    ) -> StageNode:
        """
        Create the root entrypoint node.
        
        Args:
            name: Display name
            image: Asset path for image
            audio: Asset path for audio
            
        Returns:
            Created StageNode
        """
        node = StageNode(
            uuid=generate_uuid(),
            type=NodeType.ENTRYPOINT.value,
            name=name,
            image=image,
            audio=audio
        )
        
        self.pack.stage_nodes.append(node)
        self.pack.entrypoint_uuid = node.uuid
        self._node_map[node.uuid] = node
        
        return node
    
    def create_menu(
        self,
        name: str,
        image: Optional[str] = None,
        audio: Optional[str] = None,
        parent_action_id: Optional[str] = None
    ) -> StageNode:
        """
        Create a menu node.
        
        Args:
            name: Display name
            image: Asset path for image
            audio: Asset path for audio
            parent_action_id: ID of parent ActionNode to link to
            
        Returns:
            Created StageNode
        """
        node = StageNode(
            uuid=generate_uuid(),
            type=NodeType.MENU.value,
            name=name,
            image=image,
            audio=audio
        )
        
        self.pack.stage_nodes.append(node)
        self._node_map[node.uuid] = node
        
        # Add to parent action if specified
        if parent_action_id and parent_action_id in self._action_map:
            self._action_map[parent_action_id].options.append(node.uuid)
        
        return node
    
    def create_story(
        self,
        name: str,
        audio: str,
        image: Optional[str] = None,
        nav_audio: Optional[str] = None,
        parent_action_id: Optional[str] = None
    ) -> StageNode:
        """
        Create a story node (playable audio).
        
        Args:
            name: Display name
            audio: Asset path for story audio (required)
            image: Asset path for image
            nav_audio: Asset path for navigation announcement audio
            parent_action_id: ID of parent ActionNode to link to
            
        Returns:
            Created StageNode
        """
        node = StageNode(
            uuid=generate_uuid(),
            type=NodeType.STORY.value,
            name=name,
            image=image,
            audio=nav_audio,  # Navigation audio for selection
            story_audio=audio  # Story audio for playback
        )
        
        self.pack.stage_nodes.append(node)
        self._node_map[node.uuid] = node
        
        # Add to parent action if specified
        if parent_action_id and parent_action_id in self._action_map:
            self._action_map[parent_action_id].options.append(node.uuid)
        
        return node
    
    def create_action(self, options: Optional[List[str]] = None) -> ActionNode:
        """
        Create an action node (choice point).
        
        Args:
            options: List of StageNode UUIDs as options
            
        Returns:
            Created ActionNode
        """
        action = ActionNode(
            id=generate_uuid(),
            options=options or []
        )
        
        self.pack.action_nodes.append(action)
        self._action_map[action.id] = action
        
        return action
    
    def link_node_to_action(self, stage_node: StageNode, action: ActionNode) -> None:
        """
        Set the OK transition of a stage node to an action.
        
        Args:
            stage_node: The stage node to modify
            action: The action node to link to
        """
        stage_node.ok_transition = action.id
    
    def add_option_to_action(self, action: ActionNode, stage_uuid: str) -> None:
        """
        Add a stage node as an option to an action.
        
        Args:
            action: The action node
            stage_uuid: UUID of the stage node to add
        """
        if stage_uuid not in action.options:
            action.options.append(stage_uuid)
    
    def set_night_mode(self, enabled: bool = True) -> None:
        """Enable or disable night mode."""
        self.pack.night_mode = enabled
    
    def build(self) -> StoryPack:
        """
        Finalize and return the story pack.
        
        Returns:
            The completed StoryPack
        """
        return self.pack
    
    def save(self, output_dir: str) -> bool:
        """
        Save story.json to the output directory.
        
        Args:
            output_dir: Directory to save to
            
        Returns:
            True if successful
        """
        output_path = os.path.join(output_dir, "story.json")
        return self.pack.save(output_path)


def load_story_pack(json_path: str) -> Optional[StoryPack]:
    """
    Load a StoryPack from a story.json file.
    
    Args:
        json_path: Path to story.json
        
    Returns:
        StoryPack object or None if failed
    """
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        pack = StoryPack(
            title=data.get('title', 'Unknown'),
            description=data.get('description', ''),
            format=data.get('format', 'v1'),
            version=data.get('version', 1),
            night_mode=data.get('nightMode', False)
        )
        
        # Load stage nodes
        for node_data in data.get('stageNodes', []):
            node = StageNode(
                uuid=node_data['uuid'],
                type=node_data['type'],
                name=node_data.get('name', ''),
                image=node_data.get('image'),
                audio=node_data.get('audio'),
                story_audio=node_data.get('storyAudio'),
                control_settings=node_data.get('controlSettings')
            )
            
            if 'okTransition' in node_data:
                node.ok_transition = node_data['okTransition'].get('actionNode')
                if 'optionIndex' in node_data['okTransition']:
                    node.ok_option_index = node_data['okTransition']['optionIndex']
            if 'homeTransition' in node_data:
                node.home_transition = node_data['homeTransition'].get('actionNode')
            
            pack.stage_nodes.append(node)
            
            # Track entrypoint
            if node.type == NodeType.ENTRYPOINT.value:
                pack.entrypoint_uuid = node.uuid
        
        # Load action nodes
        for action_data in data.get('actionNodes', []):
            action = ActionNode(
                id=action_data['id'],
                options=action_data.get('options', [])
            )
            pack.action_nodes.append(action)
        
        logger.info(f"Loaded story pack: {pack.title}")
        return pack
        
    except Exception as e:
        logger.error(f"Failed to load story.json: {e}")
        return None
