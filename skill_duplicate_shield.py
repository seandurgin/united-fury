"""Skill duplicate detection: prevent saving skills with overlapping triggers."""
import re
from difflib import SequenceMatcher
from skill_library import search_skills, load_skill, SKILL_CATEGORIES

def similarity_ratio(a: str, b: str) -> float:
    """
    Calculate string similarity between two strings (0.0 to 1.0).
    Uses SequenceMatcher for substring similarity.
    """
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()

def trigger_overlap(trigger1: str, trigger2: str) -> float:
    """
    Check if two trigger regex patterns would match similar requests.
    Returns overlap score (0.0 to 1.0).
    
    Heuristics:
    - Exact match: 1.0
    - Substring match: 0.8
    - Similar keywords: 0.6-0.9 (based on string similarity)
    - No overlap: 0.0
    """
    if not trigger1 or not trigger2:
        return 0.0
    
    t1_lower = trigger1.lower()
    t2_lower = trigger2.lower()
    
    # Exact match
    if t1_lower == t2_lower:
        return 1.0
    
    # One is substring of the other
    if t1_lower in t2_lower or t2_lower in t1_lower:
        return 0.8
    
    # Extract keywords (split by |, \, *, +, etc.)
    def extract_keywords(trigger):
        # Remove regex special chars, keep only word chars and spaces
        cleaned = re.sub(r'[\\()|+*?^$\[\]{}]', ' ', trigger)
        keywords = set(cleaned.lower().split())
        return keywords - {''}
    
    keywords1 = extract_keywords(trigger1)
    keywords2 = extract_keywords(trigger2)
    
    if not keywords1 or not keywords2:
        return 0.0
    
    # Jaccard similarity on keywords
    intersection = len(keywords1 & keywords2)
    union = len(keywords1 | keywords2)
    jaccard = intersection / union if union > 0 else 0.0
    
    return jaccard

def find_duplicate_skills(
    new_trigger: str,
    category: str,
    overlap_threshold: float = 0.6
) -> list:
    """
    Find existing skills that might conflict with a new trigger.
    
    Args:
        new_trigger: Regex pattern for the new skill
        category: Skill category to search in
        overlap_threshold: Minimum overlap score to flag as duplicate (0.0-1.0)
    
    Returns: List of potential duplicates
        [
            {
                "id": skill_id,
                "title": str,
                "existing_trigger": str,
                "overlap_score": float (0.6-1.0),
                "conflict_level": "high" | "medium" | "low",
            },
            ...
        ]
    """
    results = search_skills("", category, limit=1000)
    conflicts = []
    
    for skill_info in results:
        skill_id = skill_info["id"]
        existing_trigger = skill_info.get("trigger", "")
        
        overlap = trigger_overlap(new_trigger, existing_trigger)
        
        if overlap >= overlap_threshold:
            # Classify conflict severity
            if overlap >= 0.85:
                level = "high"
            elif overlap >= 0.7:
                level = "medium"
            else:
                level = "low"
            
            # Load full skill to get title
            full_skill = load_skill(skill_id, category)
            
            conflicts.append({
                "id": skill_id,
                "title": full_skill.get("title", "[untitled]") if full_skill else "[deleted]",
                "existing_trigger": existing_trigger,
                "overlap_score": round(overlap, 2),
                "conflict_level": level,
            })
    
    # Sort by overlap score (highest first)
    conflicts.sort(key=lambda x: -x["overlap_score"])
    
    return conflicts

def build_duplicate_warning(conflicts: list) -> str:
    """
    Build a user-friendly warning about potential duplicate skills.
    Returns empty string if no conflicts.
    """
    if not conflicts:
        return ""
    
    lines = [
        "",
        "⚠️  **DUPLICATE SKILL ALERT** — Found similar skills:",
        ""
    ]
    
    for i, conflict in enumerate(conflicts, 1):
        level_icon = "🔴" if conflict["conflict_level"] == "high" else "🟡"
        lines.append(
            f"{i}. {level_icon} **{conflict['title']}** "
            f"(overlap: {conflict['overlap_score']*100:.0f}%)"
        )
        lines.append(f"   Trigger: `{conflict['existing_trigger']}`")
        lines.append(f"   ID: `{conflict['id']}`")
        lines.append("")
    
    lines.extend([
        "**Consider:**",
        "- Rename your skill to avoid confusion",
        "- Merge with existing skill instead of duplicating",
        "- Use more specific trigger if intentionally different",
        "",
        "To override and save anyway, use: `skill_save override=true ...`",
    ])
    
    return "\n".join(lines)

# Test
if __name__ == "__main__":
    # Test trigger overlap
    print("=== Trigger Overlap Tests ===")
    tests = [
        ("check document title", "check document title"),  # exact
        ("check document", "check document title"),  # substring
        ("check doc", "verify document"),  # some overlap
        ("calendar.*meeting", "schedule.*meeting"),  # similar keywords
        ("send email", "transcribe"),  # no overlap
    ]
    
    for t1, t2 in tests:
        overlap = trigger_overlap(t1, t2)
        print(f"'{t1}' vs '{t2}': {overlap:.2f}")
    
    print("\n=== Duplicate Detection Test ===")
    conflicts = find_duplicate_skills("always|check|document", "clawdia", overlap_threshold=0.5)
    print(f"Found {len(conflicts)} potential duplicate(s)")
    for c in conflicts:
        print(f"  - {c['title']} (overlap: {c['overlap_score']})")
    
    if conflicts:
        warning = build_duplicate_warning(conflicts)
        print("\n--- Warning Message ---")
        print(warning)
