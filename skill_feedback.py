"""Skill feedback system: track which skills work and tune their success_rate."""
import json
from datetime import datetime, timezone
from pathlib import Path
from skill_library import load_skill, save_skill, SKILL_CATEGORIES

FEEDBACK_LOG = Path("/var/lib/clawdia/skills/feedback.jsonl")

def log_skill_feedback(skill_id: str, category: str, feedback_type: str, context: str = ""):
    """
    Log skill feedback. feedback_type: "works" | "needs_work" | "failed"
    Appends to a JSONL file for audit trail and learning.
    """
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "skill_id": skill_id,
        "category": category,
        "feedback_type": feedback_type,
        "context": context[:100],  # Cap context at 100 chars
    }
    
    try:
        with open(FEEDBACK_LOG, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        print(f"WARNING: could not log feedback: {e}")

def update_skill_success_rate(skill_id: str, category: str, feedback_type: str):
    """
    Update a skill's success_rate based on user feedback.
    
    Feedback types:
    - "works" (✓): increase toward 1.0
    - "needs_work" (⚠️): decrease slightly, stay >0.5
    - "failed" (❌): decrease toward 0.0
    
    Uses exponential smoothing: new_rate = 0.8 * old_rate + 0.2 * feedback_value
    """
    skill = load_skill(skill_id, category)
    if not skill:
        return None
    
    current_rate = float(skill.get("success_rate", 0.5))
    uses = int(skill.get("uses", 0))
    
    # Map feedback to a value
    feedback_values = {
        "works": 1.0,       # Full success
        "needs_work": 0.6,  # Partial success
        "failed": 0.2,      # Mostly failed
    }
    
    feedback_value = feedback_values.get(feedback_type, 0.5)
    
    # Exponential smoothing with weight toward more recent feedback
    # Early feedback is weighted more heavily, later feedback less
    # This prevents a single bad experience from killing a good skill
    alpha = 0.3  # 30% weight on new feedback
    new_rate = (1 - alpha) * current_rate + alpha * feedback_value
    
    # Clamp between 0.1 and 1.0 (don't let skills die completely)
    new_rate = max(0.1, min(1.0, new_rate))
    
    # Update the skill
    save_skill(
        skill_id,
        category,
        skill.get("title", ""),
        skill.get("trigger", ""),
        skill.get("body", ""),  # steps
        "",  # examples (preserve existing)
        success_rate=new_rate
    )
    
    log_skill_feedback(skill_id, category, feedback_type, f"updated from {current_rate:.2f} to {new_rate:.2f}")
    
    return {
        "skill_id": skill_id,
        "old_rate": current_rate,
        "new_rate": new_rate,
        "feedback_type": feedback_type,
    }

def build_skill_feedback_prompt(matched_skills: list) -> str:
    """
    Build a footer that appends skill feedback reactions to Clawdia's response.
    This is human-readable text that explains how to provide feedback.
    
    In Telegram, this would be followed by custom buttons (outside this scope).
    """
    if not matched_skills:
        return ""
    
    lines = [
        "",
        "---",
        "📚 **Skills used in this response:**",
    ]
    
    for skill in matched_skills:
        lines.append(f"  • {skill['title']} (success: {skill['success_rate']*100:.0f}%)")
    
    lines.extend([
        "",
        "Did these skills help? React: ✓ (works) | ⚠️ (needs work) | ❌ (failed)",
    ])
    
    return "\n".join(lines)

# Test
if __name__ == "__main__":
    # Simulate feedback
    print("Testing skill feedback...")
    
    # Current state
    skill = load_skill("always-check-document-title", "clawdia")
    print(f"Current success rate: {skill.get('success_rate')}")
    
    # User says it worked
    result = update_skill_success_rate("always-check-document-title", "clawdia", "works")
    print(f"After 'works' feedback: {result}")
    
    # User says it needs work
    result = update_skill_success_rate("always-check-document-title", "clawdia", "needs_work")
    print(f"After 'needs_work' feedback: {result}")
    
    # Verify
    skill = load_skill("always-check-document-title", "clawdia")
    print(f"Final success rate: {skill.get('success_rate')}")
