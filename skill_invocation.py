"""Skill invocation: suggest learned skills when user's message matches a skill's trigger pattern."""
import re
from skill_library import search_skills, load_skill, SKILL_CATEGORIES

def find_matching_skills(user_text: str, limit: int = 3) -> list:
    """
    Scan all skills for trigger pattern matches against user_text.
    Returns list of matched skills, ranked by success_rate (highest first).
    
    Returns: [
        {
            "id": skill_id,
            "title": str,
            "trigger": str (the regex pattern),
            "category": str,
            "steps": str (markdown steps),
            "success_rate": float,
        },
        ...
    ]
    """
    if not user_text or len(user_text) < 3:
        return []
    
    user_lower = user_text.lower()
    matches = []
    
    # Scan all skills in all categories
    for cat in SKILL_CATEGORIES:
        results = search_skills("", cat, limit=1000)  # Get all skills in category
        for skill_info in results:
            skill_id = skill_info["id"]
            trigger = skill_info.get("trigger", "")
            
            # Try to match the trigger regex against user_text
            try:
                if re.search(trigger, user_lower, re.IGNORECASE):
                    # Match found! Load the full skill
                    full_skill = load_skill(skill_id, cat)
                    if full_skill:
                        matches.append({
                            "id": skill_id,
                            "title": full_skill.get("title", ""),
                            "trigger": trigger,
                            "category": cat,
                            "steps": full_skill.get("body", ""),  # The markdown steps/examples
                            "success_rate": float(full_skill.get("success_rate", 0.5)),
                        })
            except (re.error, TypeError):
                # Malformed regex, skip
                pass
    
    # Rank by success_rate (highest first), then by category (clawdia first)
    matches.sort(key=lambda m: (-m["success_rate"], m["category"] != "clawdia"))
    
    return matches[:limit]

def build_skill_invocation_prompt(matched_skills: list) -> str:
    """
    Build a system prompt injection that tells Claude about available matching skills.
    If no matches, returns empty string.
    """
    if not matched_skills:
        return ""
    
    lines = [
        "# === SKILL INVOCATION ===",
        "You have learned skills that may be relevant to this request:",
        ""
    ]
    
    for i, skill in enumerate(matched_skills, 1):
        lines.append(f"{i}. **{skill['title']}** (success rate: {skill['success_rate']*100:.0f}%)")
        lines.append(f"   Category: {skill['category']} | ID: {skill['id']}")
        lines.append(f"   Trigger pattern: `{skill['trigger']}`")
        lines.append("")
    
    lines.extend([
        "Consider using one of these skills in your response. If you use a skill:",
        "1. Mention which skill you're applying and why it's relevant",
        "2. Reference the steps from the skill in your explanation",
        "3. If the user's request goes beyond the skill, combine the skill with additional reasoning",
        "",
        "If none of these skills are relevant, proceed with your normal response.",
        "# === END SKILL INVOCATION ===",
    ])
    
    return "\n".join(lines)

# Test
if __name__ == "__main__":
    # Simulate a user message
    test_msg = "I need to edit this document, should I check anything first?"
    
    matches = find_matching_skills(test_msg)
    print(f"Found {len(matches)} matching skill(s) for: '{test_msg}'")
    for m in matches:
        print(f"  - {m['title']} (success: {m['success_rate']})")
    
    prompt = build_skill_invocation_prompt(matches)
    if prompt:
        print("\n--- Injected prompt ---")
        print(prompt)
    else:
        print("\nNo matching skills")
