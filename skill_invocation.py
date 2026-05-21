"""Enhanced skill_invocation: suggest skills AND append feedback footer when skills are used."""
import re
from skill_library import search_skills, load_skill, SKILL_CATEGORIES

def find_matching_skills(user_text: str, limit: int = 3) -> list:
    """
    Scan all skills for trigger pattern matches against user_text.
    Returns list of matched skills, ranked by success_rate (highest first).
    """
    if not user_text or len(user_text) < 3:
        return []
    
    user_lower = user_text.lower()
    matches = []
    
    for cat in SKILL_CATEGORIES:
        results = search_skills("", cat, limit=1000)
        for skill_info in results:
            skill_id = skill_info["id"]
            trigger = skill_info.get("trigger", "")
            
            try:
                if re.search(trigger, user_lower, re.IGNORECASE):
                    full_skill = load_skill(skill_id, cat)
                    if full_skill:
                        matches.append({
                            "id": skill_id,
                            "title": full_skill.get("title", ""),
                            "trigger": trigger,
                            "category": cat,
                            "steps": full_skill.get("body", ""),
                            "success_rate": float(full_skill.get("success_rate", 0.5)),
                        })
            except (re.error, TypeError):
                pass
    
    matches.sort(key=lambda m: (-m["success_rate"], m["category"] != "clawdia"))
    return matches[:limit]

def build_skill_invocation_prompt(matched_skills: list) -> str:
    """Build system prompt injection suggesting skills."""
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

def build_skill_feedback_footer(matched_skills: list) -> str:
    """
    Build a footer that appends to Clawdia's response, offering skill feedback.
    This is shown to the user so they can provide feedback on whether skills helped.
    """
    if not matched_skills:
        return ""
    
    lines = [
        "",
        "---",
        "📚 **Skills referenced:**",
    ]
    
    for skill in matched_skills:
        lines.append(f"  • {skill['title']} (current success: {skill['success_rate']*100:.0f}%)")
    
    lines.extend([
        "",
        "Did these skills help? Provide feedback:",
        "  `skill_feedback skill_id=ID feedback=works` (✓)",
        "  `skill_feedback skill_id=ID feedback=needs_work` (⚠️)",
        "  `skill_feedback skill_id=ID feedback=failed` (❌)",
        "",
        "Feedback tunes each skill's success rate for smarter suggestions next time.",
    ])
    
    return "\n".join(lines)

if __name__ == "__main__":
    test_msg = "I'm about to edit this document"
    matches = find_matching_skills(test_msg)
    print(f"Found {len(matches)} matching skills")
    print("\n--- System prompt injection ---")
    print(build_skill_invocation_prompt(matches))
    print("\n--- Response footer ---")
    print(build_skill_feedback_footer(matches))
