"""Detect complex multi-step tasks and suggest saving as skills."""

def is_complex_task(tool_uses: list, response_length: int = 0) -> bool:
    """
    Determine if the task Clawdia just completed is complex enough to warrant skill creation.
    
    Heuristics:
    - Used 3+ tools
    - Used specific tool combinations (calendar + email + document = workflow)
    - Response was long (>500 chars, indicating complex reasoning)
    
    Args:
        tool_uses: list of tool names that were called
        response_length: character count of Clawdia's response
    
    Returns: bool, whether this should be suggested as a skill
    """
    if not tool_uses:
        return False
    
    # Simple heuristic: 3+ tools = complex
    if len(tool_uses) >= 3:
        return True
    
    # Check for specific patterns that are "multi-step workflows"
    tool_set = set(tool_uses)
    
    # Calendar + communication = workflow
    calendar_tools = {'calendar_search', 'google_calendar_add', 'google_calendar_find_slot'}
    comm_tools = {'gmail_send', 'gmail_search', 'imessage_send'}
    if tool_set & calendar_tools and tool_set & comm_tools:
        return True
    
    # Document + sheets + email = report generation workflow
    doc_tools = {'drive_read', 'drive_list', 'docx_read'}
    sheet_tools = {'sheets_read', 'sheets_write', 'sheets_update'}
    if tool_set & doc_tools and tool_set & sheet_tools and 'gmail_send' in tool_set:
        return True
    
    # Multi-account operations = complex
    if len(tool_uses) >= 2 and any('_family' in t or '_personal' in t for t in tool_uses):
        return True
    
    # Long response with moderate tool use
    if len(tool_uses) >= 2 and response_length > 800:
        return True
    
    return False

def build_skill_suggestion_prompt(tool_uses: list, response_text: str = "") -> str:
    """
    Build a prompt suggesting Sean save this as a skill.
    Shows what tools were used and the workflow.
    """
    if not tool_uses:
        return ""
    
    lines = [
        "",
        "---",
        "💡 **This looks like a workflow you might want to save as a skill!**",
        "",
        f"Steps I took: {' → '.join(tool_uses)}",
        "",
        "If you want to capture this workflow for next time, you can:",
        "  `skill_save`",
        "  `title: [Your workflow name]`",
        "  `category: work` (or personal/family/etc.)",
        "  `trigger: [keywords that would match similar requests]`",
        "  `steps: [The steps above, formatted as numbered list]`",
        "",
        "Next time you ask for something similar, I'll suggest this skill.",
    ]
    
    return "\n".join(lines)

# Test
if __name__ == "__main__":
    # Test 1: Simple single-tool task
    print("Test 1: Single tool")
    print(is_complex_task(['gmail_search']))
    print()
    
    # Test 2: Three tools = complex
    print("Test 2: Three tools")
    print(is_complex_task(['calendar_search', 'gmail_send', 'sheets_update']))
    print()
    
    # Test 3: Calendar + email workflow
    print("Test 3: Calendar + email workflow")
    print(is_complex_task(['google_calendar_find_slot', 'gmail_send']))
    print()
    
    # Test 4: Build suggestion
    print("Test 4: Suggestion prompt")
    tools = ['google_calendar_find_slot', 'gmail_send', 'gmail_search']
    print(build_skill_suggestion_prompt(tools))
