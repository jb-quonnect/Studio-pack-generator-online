"""
Studio Pack Generator Online - Pack Editor UI

Provides editing capabilities for the generated pack:
- Rename episodes/menus
- Reorder items with up/down buttons
- Delete items
- Regenerate navigation audio after changes
"""

import streamlit as st
from typing import List, Dict, Any, Optional
import os
import json

from modules.story_generator import StoryPack, StageNode, ActionNode, load_story_pack


def get_editable_structure(pack: StoryPack) -> List[Dict[str, Any]]:
    """
    Extract an editable tree structure from the pack.
    
    Returns a list of nodes with their hierarchy information.
    """
    nodes = []
    
    # Find entrypoint
    entrypoint = None
    for node in pack.stage_nodes:
        if node.type == 'entrypoint':
            entrypoint = node
            break
    
    if not entrypoint:
        return nodes
    
    # Build action map
    action_map = {action.id: action for action in pack.action_nodes}
    node_map = {node.uuid: node for node in pack.stage_nodes}
    
    def traverse(node: StageNode, depth: int = 0, parent_action_id: str = None):
        node_info = {
            'uuid': node.uuid,
            'name': node.name,
            'type': node.type,
            'depth': depth,
            'parent_action_id': parent_action_id,
            'audio': node.audio,
            'image': node.image,
            'has_children': bool(node.ok_transition)
        }
        nodes.append(node_info)
        
        # Get children via ok_transition
        if node.ok_transition and node.ok_transition in action_map:
            action = action_map[node.ok_transition]
            for child_uuid in action.options:
                if child_uuid in node_map:
                    traverse(node_map[child_uuid], depth + 1, action.id)
    
    traverse(entrypoint)
    return nodes


def move_node_in_action(pack: StoryPack, node_uuid: str, action_id: str, direction: int):
    """
    Move a node up or down within its parent action.
    
    Args:
        pack: StoryPack to modify
        node_uuid: UUID of the node to move
        action_id: ID of the parent action containing this node
        direction: -1 for up, +1 for down
    """
    # Find the action
    for action in pack.action_nodes:
        if action.id == action_id:
            if node_uuid in action.options:
                current_idx = action.options.index(node_uuid)
                new_idx = current_idx + direction
                
                # Bounds check
                if 0 <= new_idx < len(action.options):
                    # Swap positions
                    action.options[current_idx], action.options[new_idx] = \
                        action.options[new_idx], action.options[current_idx]
            break


def save_pack_changes(pack: StoryPack, story_json_path: str):
    """
    Save pack changes and regenerate ZIP.
    
    Args:
        pack: StoryPack to save
        story_json_path: Path to story.json
    """
    import os
    from modules.zip_handler import create_pack_zip
    from modules.session_manager import get_session_manager
    
    output_dir = os.path.dirname(story_json_path)
    
    # Save story.json
    pack.save(story_json_path)
    
    # Regenerate ZIP
    session = get_session_manager()
    zip_path = session.get_output_zip_path()
    
    if create_pack_zip(output_dir, zip_path):
        with open(zip_path, 'rb') as f:
            st.session_state.output_zip_data = f.read()


def apply_generated_image(pack: StoryPack, node_uuid: str, image, story_json_path: str, assets_dir: str):
    """
    Apply a generated image to a node.
    
    Args:
        pack: StoryPack to modify
        node_uuid: UUID of the node
        image: PIL Image to apply
        story_json_path: Path to story.json
        assets_dir: Directory for assets
    """
    from modules.utils import generate_uuid
    from modules.image_processor import process_image_to_asset
    
    os.makedirs(assets_dir, exist_ok=True)
    
    # Save image temporarily
    asset_name = f"gen_{generate_uuid()}"
    temp_path = os.path.join(assets_dir, f"{asset_name}_temp.png")
    image.save(temp_path, "PNG")
    
    # Process to Studio format (320x240 PNG with padding)
    final_asset = process_image_to_asset(temp_path, assets_dir)
    
    if final_asset:
        os.remove(temp_path)
        final_path = f"assets/{final_asset}"
    else:
        # Fallback: keep the PNG
        final_path = f"assets/{asset_name}.png"
        os.rename(temp_path, os.path.join(assets_dir, f"{asset_name}.png"))
    
    # Update node
    for node in pack.stage_nodes:
        if node.uuid == node_uuid:
            node.image = final_path
            break
    
    # Save changes
    pack.save(story_json_path)
    regenerate_pack_zip(os.path.dirname(story_json_path))
    st.success("✅ Image mise à jour!")


def apply_uploaded_image(pack: StoryPack, node_uuid: str, image, story_json_path: str, assets_dir: str):
    """
    Apply an uploaded image to a node.
    
    Args:
        pack: StoryPack to modify
        node_uuid: UUID of the node
        image: PIL Image to apply
        story_json_path: Path to story.json
        assets_dir: Directory for assets
    """
    from modules.utils import generate_uuid
    from modules.image_processor import process_image_to_asset
    
    os.makedirs(assets_dir, exist_ok=True)
    
    # Save uploaded image temporarily
    asset_name = f"upload_{generate_uuid()}"
    temp_path = os.path.join(assets_dir, f"{asset_name}_temp.png")
    image.save(temp_path, "PNG")
    
    # Process for Studio format
    final_asset = process_image_to_asset(temp_path, assets_dir)
    
    if final_asset:
        os.remove(temp_path)
        final_path = f"assets/{final_asset}"
    else:
        # Fallback to PNG
        final_path = f"assets/{asset_name}.png"
        os.rename(temp_path, os.path.join(assets_dir, f"{asset_name}.png"))
    
    # Update node
    for node in pack.stage_nodes:
        if node.uuid == node_uuid:
            node.image = final_path
            break
    
    # Save changes
    pack.save(story_json_path)
    regenerate_pack_zip(os.path.dirname(story_json_path))
    st.success("✅ Image importée!")

def render_pack_editor(pack: StoryPack, story_json_path: str):
    """
    Render the pack editor interface.
    
    Args:
        pack: Loaded StoryPack
        story_json_path: Path to story.json for saving changes
    """
    st.markdown("### ✏️ Modifier le pack")
    
    # Get editable structure
    nodes = get_editable_structure(pack)
    
    if not nodes:
        st.warning("Aucun élément à modifier")
        return
    
    # Initialize edit state
    if 'edit_changes' not in st.session_state:
        st.session_state.edit_changes = {}
    
    # Track if any changes were made
    changes_made = False
    
    # Display editable nodes
    for i, node in enumerate(nodes):
        if node['type'] == 'entrypoint':
            # Pack cover — show as header with image edit button
            col_icon, col_name, col_img = st.columns([0.3, 3, 0.4])
            with col_icon:
                st.markdown("📦")
            with col_name:
                st.markdown(f"**{node['name']}** *(couverture du pack)*")
            with col_img:
                if st.button("🖼️", key=f"img_{node['uuid']}", help="Modifier l'image de couverture du pack"):
                    st.session_state.editing_image_uuid = node['uuid']
                    st.rerun()
            continue

        
        indent = "│  " * (node['depth'] - 1) + "├─ " if node['depth'] > 0 else ""
        icon = "📁" if node['has_children'] else "📖"
        
        # Layout: icon | name | image btn | move btns | delete
        col1, col2, col_img, col3, col4 = st.columns([0.3, 2.5, 0.4, 0.5, 0.4])
        
        with col1:
            st.markdown(f"{indent}{icon}")
        
        with col_img:
            # Image edit button
            if st.button("🖼️", key=f"img_{node['uuid']}", help="Modifier l'image"):
                st.session_state.editing_image_uuid = node['uuid']
                st.rerun()
        
        with col2:
            # Editable name
            new_name = st.text_input(
                "Nom",
                value=node['name'],
                key=f"edit_name_{node['uuid']}",
                label_visibility="collapsed"
            )
            if new_name != node['name']:
                st.session_state.edit_changes[node['uuid']] = {
                    'name': new_name,
                    'original': node['name']
                }
                changes_made = True
        
        with col3:
            # Move up/down buttons
            col_up, col_down = st.columns(2)
            
            with col_up:
                # Move up - find siblings at same level
                can_move_up = node['parent_action_id'] is not None
                if can_move_up:
                    # Check if this is not the first item in its parent
                    siblings_before = sum(1 for j, n in enumerate(nodes[:i]) 
                                         if n['parent_action_id'] == node['parent_action_id'])
                    can_move_up = siblings_before > 0
                
                if st.button("⬆️", key=f"up_{node['uuid']}", disabled=not can_move_up):
                    move_node_in_action(pack, node['uuid'], node['parent_action_id'], -1)
                    save_pack_changes(pack, story_json_path)
                    st.rerun()
            
            with col_down:
                # Move down
                can_move_down = node['parent_action_id'] is not None
                if can_move_down:
                    # Check if this is not the last item in its parent
                    siblings_after = sum(1 for n in nodes[i+1:]
                                        if n['parent_action_id'] == node['parent_action_id'])
                    can_move_down = siblings_after > 0
                
                if st.button("⬇️", key=f"down_{node['uuid']}", disabled=not can_move_down):
                    move_node_in_action(pack, node['uuid'], node['parent_action_id'], 1)
                    save_pack_changes(pack, story_json_path)
                    st.rerun()
        
        with col4:
            # Delete button
            if st.button("🗑️", key=f"del_{node['uuid']}"):
                st.session_state.setdefault('delete_items', []).append(node['uuid'])
                st.rerun()
    
    # Image editor section
    if st.session_state.get('editing_image_uuid'):
        st.markdown("---")
        editing_uuid = st.session_state.editing_image_uuid
        
        # Find the node being edited
        editing_node = None
        for node in nodes:
            if node['uuid'] == editing_uuid:
                editing_node = node
                break
        
        if editing_node:
            st.markdown(f"### 🖼️ Modifier l'image: **{editing_node['name']}**")
            output_dir = os.path.dirname(story_json_path)
            assets_dir = os.path.join(output_dir, "assets")
            
            # Show current image if exists
            if editing_node.get('image'):
                current_img_path = os.path.join(output_dir, editing_node['image'])
                if os.path.exists(current_img_path):
                    st.image(current_img_path, caption="Image actuelle", width=160)
            
            tab_generate, tab_upload = st.tabs(["✏️ Générer texte", "📤 Uploader"])
            
            with tab_generate:
                text = st.text_input("Texte à afficher", value=editing_node['name'], key="img_gen_text")
                
                col1, col2 = st.columns(2)
                with col1:
                    bg_color = st.color_picker("Fond", "#000000", key="img_bg")
                with col2:
                    text_color = st.color_picker("Texte", "#FFFFFF", key="img_txt")
                
                font_size = st.slider("Taille", 16, 64, 32, key="img_font")
                
                if text:
                    from ui.image_editor import generate_text_image
                    preview = generate_text_image(text, 320, 240, bg_color, text_color, font_size)
                    st.image(preview, caption="Aperçu", width=320)
                    
                    if st.button("✅ Appliquer l'image", type="primary"):
                        apply_generated_image(pack, editing_uuid, preview, story_json_path, assets_dir)
                        st.session_state.editing_image_uuid = None
                        st.rerun()
            
            with tab_upload:
                uploaded = st.file_uploader("Image", type=['png', 'jpg', 'jpeg', 'bmp'], key="img_upload")
                
                if uploaded:
                    from PIL import Image
                    img = Image.open(uploaded)
                    st.image(img, caption="Aperçu", width=320)
                    
                    if st.button("✅ Utiliser cette image", type="primary"):
                        apply_uploaded_image(pack, editing_uuid, img, story_json_path, assets_dir)
                        st.session_state.editing_image_uuid = None
                        st.rerun()
            
            if st.button("❌ Annuler"):
                st.session_state.editing_image_uuid = None
                st.rerun()
    
    # Show pending changes
    if st.session_state.edit_changes or st.session_state.get('delete_items'):
        st.markdown("---")
        st.warning("⚠️ Vous avez des modifications en attente")
        
        if st.session_state.edit_changes:
            st.markdown("**Renommages:**")
            for uuid, change in st.session_state.edit_changes.items():
                st.markdown(f"- {change['original']} → **{change['name']}**")
        
        if st.session_state.get('delete_items'):
            st.markdown(f"**Suppressions:** {len(st.session_state.delete_items)} élément(s)")
        
        col_apply, col_cancel = st.columns(2)
        
        with col_apply:
            if st.button("✅ Appliquer les modifications", type="primary", use_container_width=True):
                apply_changes(pack, story_json_path)
                st.success("✅ Modifications appliquées!")
                st.session_state.edit_changes = {}
                st.session_state.delete_items = []
                st.rerun()
        
        with col_cancel:
            if st.button("❌ Annuler", use_container_width=True):
                st.session_state.edit_changes = {}
                st.session_state.delete_items = []
                st.rerun()


def apply_changes(pack: StoryPack, story_json_path: str):
    """
    Apply pending changes to the pack and save.
    Regenerates TTS audio for renamed items.
    
    Args:
        pack: StoryPack to modify
        story_json_path: Path to save changes
    """
    from modules.tts_engine import synthesize_navigation_audio
    from modules.audio_processor import process_audio_to_asset
    from modules.session_manager import get_session_manager
    
    session = get_session_manager()
    output_dir = os.path.dirname(story_json_path)
    assets_dir = os.path.join(output_dir, "assets")
    
    # Ensure assets dir exists
    os.makedirs(assets_dir, exist_ok=True)
    
    # Apply name changes and regenerate TTS
    for uuid, change in st.session_state.get('edit_changes', {}).items():
        for node in pack.stage_nodes:
            if node.uuid == uuid:
                old_name = node.name
                new_name = change['name']
                node.name = new_name
                
                # Regenerate TTS audio for this node
                st.info(f"🔊 Génération audio pour: {new_name}")
                
                temp_audio = os.path.join(session.session.input_dir, f"_tts_edit_{uuid}.mp3")
                
                if synthesize_navigation_audio(new_name, temp_audio):
                    # Process and save to assets
                    new_asset = process_audio_to_asset(
                        temp_audio,
                        assets_dir,
                        normalize=True
                    )
                    
                    if new_asset:
                        # Update node's audio reference
                        node.audio = f"assets/{new_asset}"
                        st.success(f"✅ Audio régénéré: {new_name}")
                    
                    # Clean up temp file
                    if os.path.exists(temp_audio):
                        os.remove(temp_audio)
                else:
                    st.warning(f"⚠️ Impossible de générer l'audio pour: {new_name}")
                
                break
    
    # Apply deletions
    for uuid in st.session_state.get('delete_items', []):
        # Remove from stage_nodes
        pack.stage_nodes = [n for n in pack.stage_nodes if n.uuid != uuid]
        
        # Remove from action options
        for action in pack.action_nodes:
            if uuid in action.options:
                action.options.remove(uuid)
    
    # Save to file
    try:
        story_file_path = os.path.join(output_dir, "story.json")
        pack.save(story_file_path)
        
        # Regenerate ZIP with updated story.json and new audio files
        regenerate_pack_zip(output_dir)
        
    except Exception as e:
        st.error(f"❌ Erreur lors de la sauvegarde: {e}")


def regenerate_pack_zip(output_dir: str):
    """
    Regenerate the pack ZIP after modifications.
    
    Args:
        output_dir: Directory containing the pack files
    """
    from modules.zip_handler import create_pack_zip
    from modules.session_manager import get_session_manager
    
    session = get_session_manager()
    zip_path = session.get_output_zip_path()
    
    if create_pack_zip(output_dir, zip_path):
        # Update session state with new ZIP data
        with open(zip_path, 'rb') as f:
            st.session_state.output_zip_data = f.read()
