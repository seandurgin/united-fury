"""Dedup guard for Notion write operations: check before adding to prevent duplicates."""

def check_existing_by_title(notion, database_id: str, title: str, title_property: str = "Name") -> dict:
    """
    Check if a record with the same title already exists in a Notion database.
    
    Args:
        notion: Notion client
        database_id: ID of the Notion database
        title: Title to search for (exact match)
        title_property: Property name that contains the title (default: "Name")
    
    Returns:
        {
            "exists": bool,
            "page_id": str or None,
            "created_time": str or None,
            "url": str or None,
        }
    """
    try:
        response = notion.databases.query(
            database_id=database_id,
            filter={
                "property": title_property,
                "title": {
                    "equals": title
                }
            }
        )
        
        if response.get("results") and len(response["results"]) > 0:
            page = response["results"][0]
            return {
                "exists": True,
                "page_id": page.get("id"),
                "created_time": page.get("created_time"),
                "url": page.get("url"),
            }
        
        return {
            "exists": False,
            "page_id": None,
            "created_time": None,
            "url": None,
        }
    
    except Exception as e:
        # If query fails, assume doesn't exist (safer than blocking)
        return {
            "exists": False,
            "page_id": None,
            "error": str(e),
        }

def build_dedup_warning(existing_record: dict, record_type: str = "record") -> str:
    """
    Build a user-friendly warning about existing duplicate record.
    """
    if not existing_record.get("exists"):
        return ""
    
    lines = [
        "",
        f"⚠️  **DUPLICATE {record_type.upper()} ALERT**",
        f"A {record_type} with this title already exists.",
        f"Created: {existing_record.get('created_time', 'unknown').split('T')[0] if existing_record.get('created_time') else 'unknown'}",
        f"View: {existing_record.get('url', 'N/A')}",
        "",
        "**Options:**",
        "- Proceed anyway (will create duplicate)",
        "- Modify the title to make it unique",
        "- Cancel and update the existing record instead",
    ]
    
    return "\n".join(lines)

# Test
if __name__ == "__main__":
    # Mock Notion client for testing
    class MockNotion:
        class Databases:
            def query(self, **kwargs):
                # Simulate finding a duplicate
                return {
                    "results": [
                        {
                            "id": "page123",
                            "created_time": "2026-05-20T10:30:00.000Z",
                            "url": "https://notion.so/My-Song-abc123"
                        }
                    ]
                }
        
        databases = Databases()
    
    notion = MockNotion()
    
    # Test duplicate detection
    result = check_existing_by_title(notion, "db123", "My Song Idea", "Name")
    print("Duplicate check result:", result)
    
    # Test warning
    warning = build_dedup_warning(result, "song idea")
    print("\n--- Warning Message ---")
    print(warning)
