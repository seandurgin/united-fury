"""Skill auto-cleanup: retire skills that aren't working."""
from pathlib import Path
from skill_library import load_skill, SKILL_CATEGORIES

def find_stale_skills(
    success_rate_threshold: float = 0.3,
    min_uses: int = 5,
    max_age_days: int = 7
) -> list:
    """
    Find skills that should be retired based on:
    - Low success_rate (<=threshold)
    - Used at least N times (not just one-offs)
    - Been around for at least N days (not brand new)
    
    Args:
        success_rate_threshold: Skills at or below this rate are stale (default: 0.3)
        min_uses: Only consider if used at least this many times
        max_age_days: Minimum days old before eligible for cleanup (7 days default)
    
    Returns: List of stale skill dicts
        [
            {
                "id": skill_id,
                "title": str,
                "category": str,
                "success_rate": float,
                "uses": int,
                "created": str (ISO date),
                "reason": str (why it's stale),
            },
            ...
        ]
    """
    from datetime import datetime, timedelta
    import json
    
    stale_skills = []
    cutoff_date = datetime.utcnow() - timedelta(days=max_age_days)
    
    skills_dir = Path("/var/lib/clawdia/skills")
    if not skills_dir.exists():
        return []
    
    for category in SKILL_CATEGORIES:
        cat_dir = skills_dir / category
        if not cat_dir.exists():
            continue
        
        for skill_file in cat_dir.glob("*.md"):
            skill_id = skill_file.stem
            
            try:
                skill = load_skill(skill_id, category)
                if not skill:
                    continue
                
                success_rate = float(skill.get("success_rate", 0.5))
                uses = int(skill.get("uses", 0))
                created_str = skill.get("created", "")
                
                # Parse creation date from frontmatter
                try:
                    created_date = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                except:
                    created_date = datetime.utcnow()  # Assume new if unparseable
                
                # Determine if stale
                is_old_enough = created_date < cutoff_date
                is_low_success = success_rate <= success_rate_threshold
                has_enough_uses = uses >= min_uses
                
                if is_old_enough and is_low_success and has_enough_uses:
                    reason = f"Success rate {success_rate:.0%} after {uses} uses"
                    
                    stale_skills.append({
                        "id": skill_id,
                        "title": skill.get("title", "[untitled]"),
                        "category": category,
                        "success_rate": success_rate,
                        "uses": uses,
                        "created": created_str,
                        "reason": reason,
                    })
            
            except Exception as e:
                # Skip skills with errors
                pass
    
    # Sort by success_rate (lowest first)
    stale_skills.sort(key=lambda x: x["success_rate"])
    
    return stale_skills

def build_cleanup_report(stale_skills: list) -> str:
    """
    Build a report of skills ready for cleanup.
    """
    if not stale_skills:
        return ""
    
    lines = [
        "",
        "🧹 **SKILL CLEANUP REPORT**",
        f"Found {len(stale_skills)} skill(s) ready for retirement:",
        ""
    ]
    
    for skill in stale_skills:
        lines.append(f"❌ **{skill['title']}**")
        lines.append(f"   Category: {skill['category']} | ID: `{skill['id']}`")
        lines.append(f"   Success: {skill['success_rate']:.0%} after {skill['uses']} uses")
        lines.append(f"   Reason: {skill['reason']}")
        lines.append("")
    
    lines.extend([
        "These skills are not helping. Consider retiring them.",
        "To retire a skill: `skill_retire skill_id=...`",
    ])
    
    return "\n".join(lines)

# Test
if __name__ == "__main__":
    print("Checking for stale skills...")
    stale = find_stale_skills(success_rate_threshold=0.3, min_uses=3, max_age_days=0)
    print(f"Found {len(stale)} stale skill(s)")
    
    if stale:
        report = build_cleanup_report(stale)
        print("\n--- Cleanup Report ---")
        print(report)
    else:
        print("No stale skills found")
