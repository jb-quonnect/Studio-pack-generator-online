"""
Studio Pack Generator Online - Navigation Simulator

Provides a visual simulator for testing pack navigation before download.
Allows users to:
- Navigate through menus with Left/Right/OK buttons
- Preview images and play audio
- Verify the pack structure is correct
- See auto-transition behavior matching real Lunii firmware
"""

import streamlit as st
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
import os
import base64

from modules.story_generator import StoryPack, StageNode, ActionNode, load_story_pack


@dataclass
class SimulatorState:
    """Holds the current state of the navigation simulator."""
    
    current_node_uuid: str = ""
    current_option_index: int = 0
    navigation_path: List[str] = None  # List of node names for breadcrumb
    pack: Optional[StoryPack] = None
    assets_dir: str = ""
    
    def __post_init__(self):
        if self.navigation_path is None:
            self.navigation_path = []


def init_simulator_state(pack: StoryPack, assets_dir: str) -> SimulatorState:
    """
    Initialize simulator state from a story pack.
    
    Args:
        pack: Loaded StoryPack
        assets_dir: Path to assets directory
        
    Returns:
        Initialized SimulatorState
    """
    state = SimulatorState(
        pack=pack,
        assets_dir=assets_dir
    )
    
    # Find entrypoint
    for node in pack.stage_nodes:
        if node.type == 'entrypoint':
            state.current_node_uuid = node.uuid
            state.navigation_path = [node.name]
            break
    
    return state


def get_node_by_uuid(pack: StoryPack, uuid: str) -> Optional[StageNode]:
    """Get a stage node by UUID."""
    for node in pack.stage_nodes:
        if node.uuid == uuid:
            return node
    return None


def get_action_by_id(pack: StoryPack, action_id: str) -> Optional[ActionNode]:
    """Get an action node by ID."""
    for action in pack.action_nodes:
        if action.id == action_id:
            return action
    return None


def get_current_options(pack: StoryPack, node: StageNode) -> List[StageNode]:
    """
    Get the list of option nodes for the current node.
    
    Args:
        pack: Story pack
        node: Current stage node
        
    Returns:
        List of child stage nodes
    """
    if not node.ok_transition:
        return []
    
    action = get_action_by_id(pack, node.ok_transition)
    if not action:
        return []
    
    options = []
    for option_uuid in action.options:
        option_node = get_node_by_uuid(pack, option_uuid)
        if option_node:
            options.append(option_node)
    
    return options


def render_simulator(state: SimulatorState) -> None:
    """
    Render the navigation simulator UI.
    Accurately reflects the Lunii device behavior.
    
    Args:
        state: Current simulator state
    """
    if not state.pack:
        st.warning("Aucun pack chargé pour la simulation.")
        return
    
    current_node = get_node_by_uuid(state.pack, state.current_node_uuid)
    if not current_node:
        st.error("Nœud actuel introuvable.")
        return
    
    # Get options for current node
    options = get_current_options(state.pack, current_node)
    
    # Check node control settings
    ctrl = current_node.control_settings or {}
    is_auto = ctrl.get('autoplay', False) if isinstance(ctrl, dict) else False
    has_wheel = ctrl.get('wheel', False) if isinstance(ctrl, dict) else False
    
    # Breadcrumb navigation path
    st.markdown("---")
    breadcrumb = " → ".join(state.navigation_path)
    st.markdown(f"**📍 Navigation:** {breadcrumb}")
    
    # Node type indicator
    if is_auto and options:
        st.info("⏩ **Transition automatique** — Ce nœud joue son audio puis avance automatiquement")
    elif current_node.type == 'story':
        st.success("📖 **Lecture d'histoire** — Pause ⏸️ et Home 🏠 disponibles")
    elif has_wheel and options:
        st.markdown("🎡 **Menu de sélection** — Utilisez la molette (◀️ ▶️) pour naviguer, OK pour valider")
    st.markdown("---")
    
    # Main display area
    col1, col2 = st.columns([2, 1])
    
    with col1:
        st.markdown(f"### {current_node.name}")
        st.caption(f"Type: {current_node.type}")
        
        # Display image
        if current_node.image:
            image_path = os.path.join(state.assets_dir, os.path.basename(current_node.image))
            if os.path.exists(image_path):
                st.image(image_path, width=320, caption="Écran Lunii")
            else:
                st.info("🖼️ Image non disponible")
        else:
            if current_node.type != 'story':
                st.info("🖼️ Pas d'image (nœud intermédiaire)")
    
    with col2:
        st.markdown("#### 🔊 Audio")
        
        if current_node.audio:
            audio_path = os.path.join(state.assets_dir, os.path.basename(current_node.audio))
            if os.path.exists(audio_path):
                st.audio(audio_path, format='audio/mp3')
                if current_node.type == 'story' and current_node.story_audio:
                    st.caption("🎙️ Annonce de navigation")
                else:
                    st.caption(f"🎙️ «{current_node.name}»")
            else:
                st.warning("⚠️ Fichier audio introuvable")
        else:
            st.info("🔇 Pas d'audio")
        
        # Story audio (separate from navigation audio)
        if current_node.type == 'story' and current_node.story_audio:
            story_audio_path = os.path.join(state.assets_dir, os.path.basename(current_node.story_audio))
            if os.path.exists(story_audio_path):
                st.markdown("#### 🎧 Histoire complète")
                st.audio(story_audio_path, format='audio/mp3')
    
    # Auto-transition: show skip button instead of normal navigation
    if is_auto and options:
        st.markdown("---")
        st.markdown("### ⏩ Transition automatique")
        st.caption("Sur l'appareil, le firmware enchaîne automatiquement après la lecture audio.")
        
        # Determine target
        option_index = current_node.ok_option_index if current_node.ok_option_index is not None else 0
        if option_index == -1:
            target = options[0] if options else None
            st.markdown(f"➡️ Vers le menu de sélection ({len(options)} options)")
        elif 0 <= option_index < len(options):
            target = options[option_index]
            st.markdown(f"➡️ Vers: **{target.name}**")
        else:
            target = options[0] if options else None
        
        if st.button("⏩ Simuler la transition automatique", use_container_width=True):
            if target:
                state.current_node_uuid = target.uuid
                state.current_option_index = 0
                state.navigation_path.append(target.name)
                st.rerun()
        return  # Don't show normal navigation for auto nodes
    
    # Options display (wheel mode)
    if options:
        st.markdown("---")
        
        if state.current_option_index >= len(options):
            state.current_option_index = 0
        
        selected = options[state.current_option_index]
        
        st.markdown(f"### 🎯 Sélection: **{selected.name}**")
        
        col_preview, col_list = st.columns([1, 1])
        
        with col_preview:
            if selected.image:
                img_path = os.path.join(state.assets_dir, os.path.basename(selected.image))
                if os.path.exists(img_path):
                    st.image(img_path, width=200, caption=selected.name)
            
            st.markdown("#### 🔊 Annonce")
            if selected.audio:
                audio_path = os.path.join(state.assets_dir, os.path.basename(selected.audio))
                if os.path.exists(audio_path):
                    st.audio(audio_path, format='audio/mp3')
                else:
                    st.error("❌ Audio non trouvé")
            else:
                st.warning("⚠️ Pas d'audio de navigation")
        
        with col_list:
            st.markdown(f"#### 📋 Options ({len(options)})")
            for i, option in enumerate(options):
                if i == state.current_option_index:
                    st.markdown(f"👉 **{i+1}. {option.name}** ← molette")
                else:
                    st.markdown(f"　 {i+1}. {option.name}")
    
    elif current_node.type == 'story':
        st.markdown("---")
        st.success("📖 Lecture de l'histoire en cours...")
        
        if current_node.story_audio and not current_node.audio:
            story_audio_path = os.path.join(state.assets_dir, os.path.basename(current_node.story_audio))
            if os.path.exists(story_audio_path):
                st.markdown("#### 🎧 Audio de l'histoire")
                st.audio(story_audio_path, format='audio/mp3')
        elif not current_node.story_audio and not current_node.audio:
            st.warning("⚠️ Pas d'audio pour cette histoire")
    
    else:
        st.info("Aucune option disponible depuis ce nœud.")
    
    # Navigation buttons
    st.markdown("---")
    st.markdown("### 🎮 Contrôles Lunii")
    
    col_left, col_ok, col_right, col_home = st.columns(4)
    
    with col_left:
        if st.button("⬅️ Molette ◀", use_container_width=True, disabled=len(options) <= 1):
            if options:
                state.current_option_index = (state.current_option_index - 1) % len(options)
                st.rerun()
    
    with col_ok:
        if st.button("✅ OK", use_container_width=True, disabled=len(options) == 0):
            if options:
                selected = options[state.current_option_index]
                state.current_node_uuid = selected.uuid
                state.current_option_index = 0
                state.navigation_path.append(selected.name)
                st.rerun()
    
    with col_right:
        if st.button("➡️ Molette ▶", use_container_width=True, disabled=len(options) <= 1):
            if options:
                state.current_option_index = (state.current_option_index + 1) % len(options)
                st.rerun()
    
    with col_home:
        if st.button("🏠 Home", use_container_width=True, disabled=len(state.navigation_path) <= 1):
            for node in state.pack.stage_nodes:
                if node.type == 'entrypoint':
                    state.current_node_uuid = node.uuid
                    state.current_option_index = 0
                    state.navigation_path = [node.name]
                    st.rerun()
                    break


def render_simulator_tab(output_dir: str) -> None:
    """
    Render the simulator as a Streamlit tab.
    
    Args:
        output_dir: Directory containing the generated pack
    """
    st.markdown("## 🎮 Simulateur de Navigation")
    st.markdown("Testez votre pack avant de le télécharger. Ce simulateur reproduit le comportement réel de l'appareil Lunii.")
    
    story_json_path = os.path.join(output_dir, "story.json")
    assets_dir = os.path.join(output_dir, "assets")
    
    if not os.path.exists(story_json_path):
        st.warning("Aucun pack généré. Créez d'abord votre pack.")
        return
    
    # Load pack
    pack = load_story_pack(story_json_path)
    if not pack:
        st.error("Erreur lors du chargement du pack.")
        return
    
    # Initialize or get simulator state
    if 'simulator_state' not in st.session_state:
        st.session_state.simulator_state = init_simulator_state(pack, assets_dir)
    
    state = st.session_state.simulator_state
    
    # Ensure pack is current
    state.pack = pack
    state.assets_dir = assets_dir
    
    # Render simulator
    render_simulator(state)
    
    # Reset button
    st.markdown("---")
    if st.button("🔄 Réinitialiser la simulation"):
        st.session_state.simulator_state = init_simulator_state(pack, assets_dir)
        st.rerun()


def get_pack_statistics(pack: StoryPack) -> Dict[str, Any]:
    """
    Get statistics about a pack.
    
    Args:
        pack: Story pack to analyze
        
    Returns:
        Dictionary with statistics
    """
    stats = {
        'title': pack.title,
        'total_nodes': len(pack.stage_nodes),
        'menu_count': 0,
        'story_count': 0,
        'action_count': len(pack.action_nodes),
        'max_depth': 0,
        'night_mode': pack.night_mode
    }
    
    for node in pack.stage_nodes:
        if node.type == 'menu':
            stats['menu_count'] += 1
        elif node.type == 'story':
            stats['story_count'] += 1
    
    # Calculate max depth (simplified)
    def get_depth(node_uuid: str, visited: set, depth: int = 0) -> int:
        if node_uuid in visited or depth > 20:
            return depth
        visited.add(node_uuid)
        
        node = get_node_by_uuid(pack, node_uuid)
        if not node or not node.ok_transition:
            return depth
        
        action = get_action_by_id(pack, node.ok_transition)
        if not action:
            return depth
        
        max_child_depth = depth
        for option_uuid in action.options:
            child_depth = get_depth(option_uuid, visited.copy(), depth + 1)
            max_child_depth = max(max_child_depth, child_depth)
        
        return max_child_depth
    
    # Find entrypoint and calculate depth
    for node in pack.stage_nodes:
        if node.type == 'entrypoint':
            stats['max_depth'] = get_depth(node.uuid, set())
            break
    
    return stats
