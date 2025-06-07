from datetime import datetime
from typing import Dict, Optional

class Reminder:
    def __init__(
        self,
        reminder_id: str,
        sender_id: str,
        target_id: str,
        target_type: str,
        content: str,
        target_time: datetime,
        repeat_type: str = "不重复",
        active: bool = True,
        created_at: Optional[datetime] = None
    ):
        self.id = reminder_id
        self.sender_id = sender_id
        self.target_id = target_id
        self.target_type = target_type
        self.content = content
        self.target_time = target_time
        self.repeat_type = repeat_type
        self.active = active
        self.created_at = created_at or datetime.now()

    def to_dict(self) -> Dict:
        return {
            'id': self.id,
            'sender_id': self.sender_id,
            'target_id': self.target_id,
            'target_type': self.target_type,
            'content': self.content,
            'target_time': self.target_time.isoformat(),
            'repeat_type': self.repeat_type,
            'active': self.active,
            'created_at': self.created_at.isoformat()
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'Reminder':
        return cls(
            reminder_id=data['id'],
            sender_id=data['sender_id'],
            target_id=data['target_id'],
            target_type=data['target_type'],
            content=data['content'],
            target_time=datetime.fromisoformat(data['target_time']),
            repeat_type=data.get('repeat_type', '不重复'),
            active=data.get('active', True),
            created_at=datetime.fromisoformat(data['created_at'])
        ) 