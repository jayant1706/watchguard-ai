"""
WatchGuard AI - Session Logger v2
Tracks video position timestamps (not wall clock) so you can jump back
to the exact movie moment you missed.
"""

import json
import csv
from datetime import datetime
from pathlib import Path


class AwayEvent:
    """One complete away -> return cycle with full context."""
    
    def __init__(self, event_id: int, wall_time: str, video_pos_seconds):
        self.event_id          = event_id
        self.wall_away         = wall_time
        self.wall_return       = None
        self.video_pos_away    = video_pos_seconds   # seconds into the movie when left
        self.video_pos_return  = None                # seconds into the movie when returned
        self.away_duration     = None                # real seconds the user was gone
        self.complete          = False

    def close(self, wall_return: str, real_away_seconds: float, video_pos_return):
        self.wall_return       = wall_return
        self.video_pos_return  = video_pos_return
        self.away_duration     = round(real_away_seconds, 2)
        self.complete          = True

    def to_dict(self):
        return {
            "event_id"              : self.event_id,
            "went_away_at_wall"     : self.wall_away,
            "returned_at_wall"      : self.wall_return,
            "video_position_away_s" : self.video_pos_away,
            "video_position_away"   : fmt_video(self.video_pos_away),
            "video_position_return_s": self.video_pos_return,
            "video_position_return" : fmt_video(self.video_pos_return) if self.video_pos_return is not None else None,
            "real_away_seconds"     : self.away_duration,
            "real_away_formatted"   : fmt_video(self.away_duration) if self.away_duration else None,
            "complete"              : self.complete,
        }

    def resume_label(self):
        pos = fmt_video(self.video_pos_away)
        dur = fmt_video(self.away_duration) if self.away_duration else "?"
        return f"#{self.event_id}   Missed at {pos}   ({dur} away)"


class SessionLogger:
    def get_last_event(self):
        if not self.away_events:
            return None
        return self.away_events[-1]
    def __init__(self):
        self.away_events   = []          # list[AwayEvent]
        self._open_event   = None        # currently open (user is away)
        self._event_counter = 0
        self._away_wall_start = None     # time.time() when away started
        self.session_start_wall = datetime.now().isoformat()
        self.session_id    = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.last_saved    = None
        self.log_dir       = Path.home() / "WatchGuard_Logs"
        self.log_dir.mkdir(exist_ok=True)

    # ── Public API ────────────────────────────────────────────────────

    def log_away(self, video_pos_seconds, wall_start_time: float):
        """
        Call when the user goes away.
        video_pos_seconds : playback position at moment of going away (float or None)
        wall_start_time   : time.time() value of when away started
        """
        if self._open_event is not None:
            return  # already tracking

        self._event_counter += 1
        wall = datetime.now().strftime("%H:%M:%S")
        event = AwayEvent(self._event_counter, wall, video_pos_seconds)
        self._open_event = event
        self._away_wall_start = wall_start_time
        self.away_events.append(event)

    def log_return(self, video_pos_seconds):
        """
        Call when the user returns.
        video_pos_seconds : playback position at moment of returning (float or None)
        Returns the closed AwayEvent or None.
        """
        if self._open_event is None:
            return None

        import time
        real_away = time.time() - self._away_wall_start if self._away_wall_start else 0
        wall = datetime.now().strftime("%H:%M:%S")
        self._open_event.close(wall, real_away, video_pos_seconds)
        closed = self._open_event
        self._open_event = None
        self._away_wall_start = None
        return closed

    def get_resume_events(self):
        """Return all complete away events sorted by video position."""
        return sorted(
            [e for e in self.away_events if e.complete and e.video_pos_away is not None],
            key=lambda e: e.video_pos_away
        )

    def total_missed_seconds(self):
        return sum(
            e.away_duration for e in self.away_events
            if e.complete and e.away_duration is not None
        )

    # ── Persistence ───────────────────────────────────────────────────

    def save_session(self):
        filename = self.log_dir / f"session_{self.session_id}.json"
        data = {
            "session_id"          : self.session_id,
            "session_start"       : self.session_start_wall,
            "session_end"         : datetime.now().isoformat(),
            "total_away_events"   : len(self.away_events),
            "total_real_away_secs": round(self.total_missed_seconds(), 2),
            "events"              : [e.to_dict() for e in self.away_events],
        }
        with open(filename, "w") as f:
            json.dump(data, f, indent=2)
        self.last_saved = str(filename)
        return str(filename)

    def export_csv(self):
        filename = self.log_dir / f"session_{self.session_id}.csv"
        fields = [
            "event_id", "went_away_at_wall", "returned_at_wall",
            "video_position_away", "video_position_away_s",
            "video_position_return", "video_position_return_s",
            "real_away_seconds", "real_away_formatted",
        ]
        with open(filename, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            for e in self.away_events:
                writer.writerow(e.to_dict())
        return str(filename)


def fmt_video(seconds):
    """Format seconds -> H:MM:SS or MM:SS."""
    if seconds is None:
        return "?"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s   = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"
