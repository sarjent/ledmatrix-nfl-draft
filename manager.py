"""
NFL Draft Plugin for LEDMatrix

Displays projected and live NFL draft picks from ESPN API.
Supports dual-mode operation: projections (off-season) and live tracking (during draft).

Features:
- Projected draft picks from ESPN (mock draft data)
- Live draft tracking during the NFL Draft event
- Automatic mode switching between projections and live
- Configurable rounds, fonts, colors
- Smooth horizontal scrolling through picks
- Team logos displayed alongside player names

API Version: 1.0.0
"""

import concurrent.futures
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from urllib.request import urlopen, Request
from urllib.error import URLError
import json

from PIL import Image, ImageDraw, ImageFont

from src.plugin_system.base_plugin import BasePlugin, VegasDisplayMode
from src.common.scroll_helper import ScrollHelper
from src.common.logo_helper import LogoHelper
from src.common.api_helper import APIHelper

logger = logging.getLogger(__name__)


class NFLDraftPlugin(BasePlugin):
    """
    NFL Draft plugin that displays projected and live draft picks.

    Features:
    - Projected draft picks from ESPN (mock draft data)
    - Live draft tracking during the NFL Draft event
    - Automatic mode switching between projections and live
    - Configurable rounds, fonts, colors
    - Smooth horizontal scrolling through picks
    - Team logos displayed alongside player names
    """

    # ESPN API Endpoints
    # Site API provides mock draft with team projections (pre-draft)
    ESPN_DRAFT_SITE = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/draft"
    # Core API provides detailed athlete data and actual draft results (post-draft)
    ESPN_DRAFT_CORE = "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/seasons/{year}/draft"
    ESPN_DRAFT_ATHLETES = "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/seasons/{year}/draft/athletes"

    def __init__(self, plugin_id: str, config: Dict[str, Any],
                 display_manager, cache_manager, plugin_manager):
        """Initialize the NFL Draft plugin."""
        super().__init__(plugin_id, config, display_manager, cache_manager, plugin_manager)

        # Display dimensions
        self.display_width = display_manager.matrix.width
        self.display_height = display_manager.matrix.height

        # Initialize helpers
        self.scroll_helper = ScrollHelper(self.display_width, self.display_height, self.logger)
        self.logo_helper = LogoHelper(self.display_width, self.display_height, logger=self.logger)
        self.api_helper = APIHelper(cache_manager, logger=self.logger)

        # Load configuration
        self._load_config()

        # Data storage
        self.draft_picks: List[Dict[str, Any]] = []
        self.is_draft_live = False
        self.draft_status = "unknown"  # "pre", "live", "complete"
        self.current_round = 1
        self.last_update_time: Optional[float] = None
        self.last_live_check_time: Optional[float] = None

        # Font loading - separate sizes for player name vs details
        self.player_name_font = self._load_font(self.player_name_font_size)
        self.detail_font = self._load_font(self.detail_font_size)

        # Logo path (using core LEDMatrix assets)
        self.logo_base_path = Path("assets/sports/nfl_logos")

        # Ensure NFL Draft logo is installed and load it for the scroll header
        self._ensure_logo_installed()
        self.nfl_draft_logo = self._load_nfl_draft_logo()

        self.logger.info(f"NFL Draft plugin initialized for year {self.draft_year}")

    def _load_config(self) -> None:
        """Load and parse configuration values."""
        # Rounds configuration
        rounds_str = self.config.get("rounds", "1,2,3")
        if rounds_str.lower() == "all":
            self.rounds_to_display = list(range(1, 8))  # Rounds 1-7
        else:
            try:
                self.rounds_to_display = [
                    int(r.strip()) for r in rounds_str.split(",")
                    if r.strip().isdigit()
                ]
            except (ValueError, AttributeError):
                self.rounds_to_display = [1, 2, 3]

        if not self.rounds_to_display:
            self.rounds_to_display = [1, 2, 3]

        # Font settings
        self.font_name = self.config.get("font", "PressStart2P-Regular.ttf")
        self.player_name_font_size = self.config.get("player_name_font_size", 12)
        self.detail_font_size = self.config.get("detail_font_size", 8)

        # Color settings
        player_color = self.config.get("player_name_color", {"r": 255, "g": 255, "b": 255})
        self.player_color = (
            player_color.get("r", 255),
            player_color.get("g", 255),
            player_color.get("b", 255)
        )

        pick_color = self.config.get("pick_number_color", {"r": 255, "g": 255, "b": 255})
        self.pick_color = (
            pick_color.get("r", 255),
            pick_color.get("g", 255),
            pick_color.get("b", 255)
        )

        # Scroll settings
        self.scroll_speed = self.config.get("scroll_speed", 30)
        self.scroll_helper.set_scroll_speed(self.scroll_speed)

        # Refresh intervals
        self.live_refresh_interval = self.config.get("live_refresh_interval", 600)  # 10 minutes
        self.projection_refresh_interval = self.config.get("projection_refresh_interval", 86400)  # 24 hours

        # Display settings
        self.show_position = self.config.get("show_position", True)
        self.show_college = self.config.get("show_college", True)
        self.item_gap = self.config.get("item_gap", 32)

        # Logo size - 0 means auto-size based on display height (like NFL Scoreboard)
        logo_size_config = self.config.get("logo_size", 0)
        if logo_size_config == 0:
            # Auto-size: use display height (fills vertical space)
            self.logo_size = self.display_height
        else:
            self.logo_size = logo_size_config

        # Dynamic duration settings
        dynamic_duration = self.config.get("dynamic_duration", {})
        self.dynamic_duration_enabled = dynamic_duration.get("enabled", True)
        self.min_duration = dynamic_duration.get("min_duration", 30)
        self.max_duration = dynamic_duration.get("max_duration", 300)

        # Configure scroll helper dynamic duration
        self.scroll_helper.set_dynamic_duration_settings(
            enabled=self.dynamic_duration_enabled,
            min_duration=self.min_duration,
            max_duration=self.max_duration,
            buffer=0.1
        )

        # Draft year (0 = auto-detect current/upcoming)
        self.draft_year = self.config.get("draft_year", 0)
        if self.draft_year == 0:
            self.draft_year = self._get_current_draft_year()

        # Simulation settings — override draft_year when active
        self.simulate_live = self.config.get("simulate_live", False)
        self.simulate_year = self.config.get("simulate_year", 2025)
        if self.simulate_live:
            self.draft_year = self.simulate_year

        # Favorite teams for live-mode highlights (up to 3 abbreviations)
        fav_raw = self.config.get("favorite_teams", [])
        if isinstance(fav_raw, list):
            self.favorite_teams = [str(t).upper().strip() for t in fav_raw if t][:3]
        else:
            self.favorite_teams = []

    def _load_font(self, size: int) -> ImageFont.ImageFont:
        """Load configured font at specified size."""
        try:
            font_path = Path("assets/fonts") / self.font_name
            if font_path.exists():
                return ImageFont.truetype(str(font_path), size)
        except Exception as e:
            self.logger.warning(f"Could not load font {self.font_name} at size {size}: {e}")

        return ImageFont.load_default()

    def _get_current_draft_year(self) -> int:
        """Determine the current/upcoming draft year."""
        now = datetime.now()
        # If before May, show current year's draft
        # If May or later, show next year's draft
        if now.month < 5:
            return now.year
        return now.year + 1

    def _fetch_draft_data(self) -> Dict[str, Any]:
        """
        Fetch draft data from ESPN site API.

        This endpoint provides mock draft picks with team projections (pre-draft)
        or actual draft results (post-draft).
        """
        cache_key = f"nfl_draft_site_{self.draft_year}"
        cache_ttl = self.live_refresh_interval if self.is_draft_live else self.projection_refresh_interval

        data = self.api_helper.get(
            self.ESPN_DRAFT_SITE,
            cache_key=cache_key,
            cache_ttl=cache_ttl
        )
        return data or {}

    def _fetch_all_prospects(self) -> List[Dict[str, Any]]:
        """
        Fetch all draft prospects from ESPN core API.

        This fetches the full list of draft-eligible athletes and their rankings,
        allowing us to build a complete mock draft by matching prospects to picks.

        Returns:
            List of prospect dictionaries sorted by overall rank
        """
        cache_key = f"nfl_draft_prospects_{self.draft_year}"

        # Check cache first
        cached_data = self.cache_manager.get(cache_key)
        if cached_data:
            self.logger.debug("Using cached prospect data")
            return cached_data

        prospects = []

        try:
            # Get list of all draft athletes
            athletes_url = self.ESPN_DRAFT_ATHLETES.format(year=self.draft_year)
            athletes_url += "?limit=300"  # Get up to 300 prospects

            self.logger.info(f"Fetching draft athletes list from {athletes_url}")

            req = Request(athletes_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urlopen(req, timeout=30) as response:
                data = json.loads(response.read().decode())

            items = data.get("items", [])
            self.logger.info(f"Found {len(items)} athlete references")

            if not items:
                return prospects

            # Extract athlete URLs from references
            athlete_urls = []
            for item in items:
                url = item.get("$ref")
                if url:
                    athlete_urls.append(url)

            # Fetch athlete details in parallel (limit concurrency)
            def fetch_athlete(url: str) -> Optional[Dict[str, Any]]:
                try:
                    req = Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                    with urlopen(req, timeout=10) as response:
                        return json.loads(response.read().decode())
                except Exception as e:
                    self.logger.debug(f"Failed to fetch athlete: {e}")
                    return None

            # Use ThreadPoolExecutor for parallel fetching
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                athlete_data = list(executor.map(fetch_athlete, athlete_urls[:150]))  # Limit to top 150

            # Process athlete data
            for athlete in athlete_data:
                if not athlete:
                    continue

                # Extract overall rank from attributes
                overall_rank = 999
                for attr in athlete.get("attributes", []):
                    if attr.get("name") == "overall":
                        try:
                            overall_rank = int(float(attr.get("value", 999)))
                        except (ValueError, TypeError):
                            pass

                # Get position
                position = athlete.get("position", {})
                pos_abbr = position.get("abbreviation", "") if isinstance(position, dict) else ""

                # Get college team
                college = ""
                college_team = athlete.get("team", {})
                if college_team:
                    college = college_team.get("shortDisplayName", college_team.get("name", ""))

                prospect = {
                    "id": athlete.get("id"),
                    "displayName": athlete.get("displayName", "Unknown"),
                    "position": pos_abbr,
                    "college": college,
                    "overall_rank": overall_rank
                }
                prospects.append(prospect)

            # Sort by overall rank
            prospects.sort(key=lambda x: x.get("overall_rank", 999))

            self.logger.info(f"Fetched and ranked {len(prospects)} prospects")

            # Cache the results
            if prospects:
                self.cache_manager.set(cache_key, prospects, ttl=self.projection_refresh_interval)

        except Exception as e:
            self.logger.error(f"Error fetching prospects: {e}", exc_info=True)

        return prospects

    def _fetch_nfl_teams(self) -> Dict[str, str]:
        """
        Fetch NFL team ID → abbreviation mapping from ESPN site API.

        Returns:
            Dict mapping team ID string to team abbreviation (e.g. {'10': 'KC'})
        """
        cache_key = "nfl_teams_lookup"
        cached = self.cache_manager.get(cache_key)
        if cached:
            return cached

        teams: Dict[str, str] = {}
        try:
            url = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams?limit=50"
            req = Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urlopen(req, timeout=15) as response:
                data = json.loads(response.read().decode())

            # Response: {"sports": [{"leagues": [{"teams": [...]}]}]}
            for entry in (data.get("sports", [{}])[0]
                          .get("leagues", [{}])[0]
                          .get("teams", [])):
                team = entry.get("team", {})
                team_id = str(team.get("id", ""))
                abbr = team.get("abbreviation", "")
                if team_id and abbr:
                    teams[team_id] = abbr

            self.logger.info(f"Fetched {len(teams)} NFL team abbreviations")
            if teams:
                self.cache_manager.set(cache_key, teams, ttl=86400)

        except Exception as e:
            self.logger.error(f"Error fetching NFL teams: {e}")

        return teams

    def _fetch_historical_picks(self) -> List[Dict[str, Any]]:
        """
        Fetch completed draft picks from ESPN core API for simulate_live mode.

        The core API /draft/rounds endpoint returns all round objects inline in
        the items array — each item contains its full picks list directly.
        Athlete $ref URLs are resolved in parallel for player name and position.

        Returns:
            List of pick dicts in the same format as _fetch_draft_picks()
        """
        year = self.simulate_year
        cache_key = f"nfl_draft_historical_{year}"
        cached = self.cache_manager.get(cache_key)
        if cached:
            self.logger.debug(f"Using cached historical picks for {year}")
            return cached

        teams_lookup = self._fetch_nfl_teams()
        picks: List[Dict[str, Any]] = []

        try:
            # Picks are inline in the rounds list — each item is a full round object
            rounds_url = (
                f"https://sports.core.api.espn.com/v2/sports/football/leagues/nfl"
                f"/seasons/{year}/draft/rounds?lang=en&region=us&limit=10"
            )
            req = Request(rounds_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urlopen(req, timeout=15) as response:
                rounds_data = json.loads(response.read().decode())

            # Collect picks from matching rounds directly from the inline items
            raw_picks: List[Tuple[int, Dict]] = []
            for item in rounds_data.get("items", []):
                round_num = item.get("number", 0)
                if round_num in self.rounds_to_display:
                    for pick in item.get("picks", []):
                        raw_picks.append((round_num, pick))

            self.logger.info(f"Found {len(raw_picks)} picks across configured rounds for {year} draft")

            # Resolve athlete $ref URLs in parallel (index-aligned with raw_picks)
            athlete_urls = [
                pick.get("athlete", {}).get("$ref", "") for (_rn, pick) in raw_picks
            ]

            def fetch_athlete_ref(url: str) -> Optional[Dict]:
                if not url:
                    return None
                try:
                    req = Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                    with urlopen(req, timeout=10) as response:
                        return json.loads(response.read().decode())
                except Exception as e:
                    self.logger.debug(f"Failed to fetch athlete ref: {e}")
                    return None

            with concurrent.futures.ThreadPoolExecutor(max_workers=15) as executor:
                athlete_results = list(executor.map(fetch_athlete_ref, athlete_urls))

            # Build standardised pick dicts
            for i, (round_num, raw_pick) in enumerate(raw_picks):
                athlete = athlete_results[i]

                # Extract team ID from $ref URL — strip query params before splitting
                team_ref = raw_pick.get("team", {}).get("$ref", "")
                team_id = team_ref.split("?")[0].rstrip("/").split("/")[-1] if team_ref else ""
                team_abbr = teams_lookup.get(team_id, "")

                player_name = "TBD"
                position = ""
                if athlete:
                    player_name = athlete.get("displayName", "TBD")
                    pos = athlete.get("position", {})
                    if isinstance(pos, dict):
                        position = pos.get("abbreviation", "")
                    # Note: athlete.college and athlete.team are $ref objects in the
                    # draft API; college name is not available without extra fetches.

                pick_data: Dict[str, Any] = {
                    "pick_number": raw_pick.get("overall", i + 1),
                    "round": round_num,
                    "round_pick": raw_pick.get("pick", 0),
                    "team_abbr": team_abbr,
                    "team_name": "",
                    "player_name": player_name,
                    "position": position,
                    "college": "",
                }

                if pick_data["team_abbr"] or pick_data["player_name"] != "TBD":
                    picks.append(pick_data)

            picks.sort(key=lambda x: x.get("pick_number", 0))
            self.logger.info(f"Fetched {len(picks)} historical picks for {year}")

            if picks:
                self.cache_manager.set(cache_key, picks, ttl=86400)

        except Exception as e:
            self.logger.error(f"Error fetching historical picks: {e}", exc_info=True)

        return picks

    def _fetch_draft_picks(self, round_num: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Fetch draft picks from ESPN site API.

        For pre-draft: Builds mock draft by matching top prospects with draft order.
        For live/post-draft: Uses actual draft pick data.

        Args:
            round_num: Specific round to fetch, or None for all configured rounds

        Returns:
            List of draft pick dictionaries
        """
        picks = []

        data = self._fetch_draft_data()

        if not data:
            self.logger.warning("No draft data returned from ESPN API")
            return picks

        # Update draft status from the response
        status = data.get("status", {})
        if status:
            state = status.get("state", "").lower()
            if state == "in":
                self.draft_status = "live"
                self.is_draft_live = True
            elif state == "post":
                self.draft_status = "complete"
            else:
                self.draft_status = "pre"

            # Get current round from status
            current_round = status.get("round", 1)
            if isinstance(current_round, int):
                self.current_round = current_round

        # Build team lookup (teamId -> team info)
        teams_lookup = {}
        for team in data.get("teams", []):
            team_id = team.get("id")
            if team_id:
                teams_lookup[str(team_id)] = team

        # Get draft order from picks
        raw_picks = data.get("picks", [])
        self.logger.info(f"Found {len(raw_picks)} picks in ESPN response")

        # Get all prospects for mock draft (pre-draft mode)
        # Fetch from core API to get full prospect list with rankings
        prospects = self._fetch_all_prospects() if self.draft_status == "pre" else []
        self.logger.info(f"Found {len(prospects)} prospects for mock draft")

        # Build picks list
        for idx, raw_pick in enumerate(raw_picks):
            pick_number = raw_pick.get("overall", idx + 1)
            pick_round = raw_pick.get("round", 1)

            # Filter by round
            if round_num is not None and pick_round != round_num:
                continue
            if pick_round not in self.rounds_to_display:
                continue

            # Get team info
            team_id = str(raw_pick.get("teamId", ""))
            team_info = teams_lookup.get(team_id, {})

            pick_data = {
                "pick_number": pick_number,
                "round": pick_round,
                "round_pick": raw_pick.get("pick", 0),
                "team_abbr": team_info.get("abbreviation", ""),
                "team_name": team_info.get("displayName", ""),
                "player_name": "TBD",
                "position": "",
                "college": ""
            }

            # For pre-draft, match with prospect by rank
            if self.draft_status == "pre" and idx < len(prospects):
                prospect = prospects[idx]
                pick_data["player_name"] = prospect.get("displayName", "TBD")
                pick_data["position"] = prospect.get("position", "")
                pick_data["college"] = prospect.get("college", "")

            # For live/post draft, use athlete data if available
            elif raw_pick.get("athlete"):
                athlete = raw_pick["athlete"]
                pick_data["player_name"] = athlete.get("displayName", "TBD")
                position = athlete.get("position", {})
                if isinstance(position, dict):
                    pick_data["position"] = position.get("abbreviation", "")
                college_team = athlete.get("team", {})
                if college_team and isinstance(college_team, dict):
                    pick_data["college"] = college_team.get("shortDisplayName", college_team.get("name", ""))

            # Only add if we have meaningful data
            if pick_data["team_abbr"] or pick_data["player_name"] != "TBD":
                picks.append(pick_data)

        return picks

    def _check_draft_live_status(self) -> bool:
        """
        Check if the NFL Draft is currently live.

        Uses the site API status field which is updated in _fetch_draft_picks.
        Falls back to date-based detection.

        Returns:
            True if draft is live, False otherwise
        """
        # First try to get status from site API
        data = self._fetch_draft_data()

        if data:
            status = data.get("status", {})
            if status:
                state = status.get("state", "").lower()
                if state == "in":
                    self.draft_status = "live"
                    return True
                elif state == "post":
                    self.draft_status = "complete"
                    return False
                else:
                    self.draft_status = "pre"
                    return False

        # Fallback: check by date
        return self._is_draft_date()

    def _is_draft_date(self) -> bool:
        """Check if current date is during NFL Draft (late April)."""
        now = datetime.now()
        # NFL Draft typically occurs last week of April (Thursday-Saturday)
        draft_start = datetime(self.draft_year, 4, 20)
        draft_end = datetime(self.draft_year, 4, 27)

        return draft_start <= now <= draft_end

    def _get_display_round(self) -> Tuple[int, List[Dict[str, Any]]]:
        """
        Determine which round to show during a live draft.

        Returns the current round if it has at least one completed selection;
        otherwise falls back to the highest round that has completed picks.
        This handles the gap between rounds where current_round has advanced
        but no selections have been announced yet.

        Returns:
            (round_number, picks_list)
        """
        current_picks = [p for p in self.draft_picks if p.get("round") == self.current_round]
        current_done = [p for p in current_picks if p.get("player_name", "TBD") != "TBD"]

        if current_done:
            return self.current_round, current_picks

        # No selections yet in current_round — show last completed round
        completed_rounds = sorted(
            {p.get("round", 0) for p in self.draft_picks
             if p.get("player_name", "TBD") != "TBD"},
            reverse=True
        )
        if completed_rounds:
            last = completed_rounds[0]
            return last, [p for p in self.draft_picks if p.get("round") == last]

        return self.current_round, current_picks  # fallback

    def _get_favorite_team_picks(self) -> List[Dict[str, Any]]:
        """
        Return the up to 3 most recently made picks from configured favorite teams.

        Only includes picks with real player names (not TBD), sorted by pick
        number descending so the most recent selection appears first.
        """
        if not self.favorite_teams:
            return []
        fav = [
            p for p in self.draft_picks
            if p.get("team_abbr", "").upper() in self.favorite_teams
            and p.get("player_name", "TBD") != "TBD"
        ]
        fav.sort(key=lambda x: x.get("pick_number", 0), reverse=True)
        return fav[:3]

    def _create_round_label_item(self, round_num: int) -> Image.Image:
        """Create a scroll item showing 'ROUND X' as a section header in gold."""
        text = f"ROUND {round_num}"
        temp_img = Image.new('RGB', (1, 1))
        temp_draw = ImageDraw.Draw(temp_img)
        try:
            w = int(temp_draw.textlength(text, font=self.player_name_font))
        except Exception:
            bbox = temp_draw.textbbox((0, 0), text, font=self.player_name_font)
            w = bbox[2] - bbox[0]
        img = Image.new('RGB', (max(w, 1), self.display_height), (0, 0, 0))
        draw = ImageDraw.Draw(img)
        y = (self.display_height - self.player_name_font_size) // 2
        draw.text((0, y), text, font=self.player_name_font, fill=(255, 200, 0))
        return img

    def _create_draft_scroll_image(self) -> None:
        """Create scrolling image with all draft picks."""
        content_items = []

        # NFL Draft logo always leads the scroll
        if self.nfl_draft_logo:
            content_items.append(self.nfl_draft_logo)

        if self.is_draft_live:
            # Smart round selection: current round if it has selections, else last completed
            display_round, round_picks = self._get_display_round()
            content_items.append(self._create_round_label_item(display_round))

            # Favorite team picks — up to 3 most recent, prepended before round picks
            for pick in self._get_favorite_team_picks():
                img = self._create_pick_item(pick)
                if img:
                    content_items.append(img)

            for pick in round_picks:
                img = self._create_pick_item(pick)
                if img:
                    content_items.append(img)

        elif self.simulate_live:
            # Simulation: all configured rounds with favorite picks at the front
            picks_to_display = [p for p in self.draft_picks if p["round"] in self.rounds_to_display]

            for pick in self._get_favorite_team_picks():
                img = self._create_pick_item(pick)
                if img:
                    content_items.append(img)

            for pick in picks_to_display:
                img = self._create_pick_item(pick)
                if img:
                    content_items.append(img)

        else:
            # Pre-draft projections: all configured rounds, no extras
            for pick in [p for p in self.draft_picks if p["round"] in self.rounds_to_display]:
                img = self._create_pick_item(pick)
                if img:
                    content_items.append(img)

        if content_items:
            self.scroll_helper.create_scrolling_image(
                content_items,
                item_gap=self.item_gap,
                element_gap=8
            )
            self.logger.info(f"Created scroll image with {len(content_items)} items")
        else:
            self.logger.warning("No draft picks to display")

    def _create_pick_item(self, pick: Dict[str, Any]) -> Optional[Image.Image]:
        """
        Create a single pick item image with logo, name, position, and pick number.

        Layout (two lines):
            [LOGO] Player Name
                   POS  #PICK  (School)

        Args:
            pick: Pick data dictionary

        Returns:
            PIL Image for the pick item
        """
        item_height = self.display_height

        # Load team logo
        team_abbr = pick.get("team_abbr", "").upper()
        logo = self._load_team_logo(team_abbr)
        logo_width = logo.width if logo else 0

        # Player name (large font) - top line
        on_clock = pick.get("on_clock", False)
        if on_clock:
            player_name = "On the Clock"
            name_color = (0, 200, 0)
        else:
            player_name = pick.get("player_name", "TBD")
            name_color = self.player_color

        # Build detail line: #PICK  POS  (College)
        detail_parts = []

        # Overall pick number
        detail_parts.append(f"#{pick.get('pick_number', 0)}")

        # Position
        if self.show_position and pick.get("position"):
            detail_parts.append(pick["position"])

        # College (optional)
        if self.show_college and pick.get("college"):
            detail_parts.append(f"({pick['college']})")

        detail_text = "  ".join(detail_parts)

        # Calculate text widths using temp draw context
        temp_img = Image.new('RGB', (1, 1))
        temp_draw = ImageDraw.Draw(temp_img)

        try:
            player_name_width = int(temp_draw.textlength(player_name, font=self.player_name_font))
            detail_width = int(temp_draw.textlength(detail_text, font=self.detail_font))
        except Exception:
            # Fallback for older PIL versions
            player_bbox = temp_draw.textbbox((0, 0), player_name, font=self.player_name_font)
            player_name_width = player_bbox[2] - player_bbox[0]
            detail_bbox = temp_draw.textbbox((0, 0), detail_text, font=self.detail_font)
            detail_width = detail_bbox[2] - detail_bbox[0]

        # Calculate total item width (max of player name or detail line, plus logo)
        element_spacing = 6
        text_width = max(player_name_width, detail_width)
        total_width = logo_width + element_spacing + text_width

        # Create item image
        item_img = Image.new('RGB', (total_width, item_height), (0, 0, 0))
        draw = ImageDraw.Draw(item_img)

        current_x = 0

        # Paste logo (left side, vertically centered)
        if logo:
            logo_y = (item_height - logo.height) // 2
            if logo.mode == 'RGBA':
                item_img.paste(logo, (current_x, logo_y), logo)
            else:
                item_img.paste(logo, (current_x, logo_y))
            current_x += logo_width + element_spacing

        text_start_x = current_x

        # Calculate vertical positions for two-line layout
        # Total text height = player name + small gap + detail line
        line_gap = 2
        total_text_height = self.player_name_font_size + line_gap + self.detail_font_size

        # Center the two lines vertically
        top_y = (item_height - total_text_height) // 2
        player_name_y = top_y
        detail_y = top_y + self.player_name_font_size + line_gap

        # Draw player name (large font, top line)
        draw.text((text_start_x, player_name_y), player_name, font=self.player_name_font, fill=name_color)

        # Draw detail line (small font, bottom line)
        draw.text((text_start_x, detail_y), detail_text, font=self.detail_font, fill=self.pick_color)

        return item_img

    def _load_team_logo(self, team_abbr: str) -> Optional[Image.Image]:
        """Load and resize team logo."""
        if not team_abbr:
            return None

        logo_path = self.logo_base_path / f"{team_abbr}.png"

        logo = self.logo_helper.load_logo(
            team_abbr,
            logo_path,
            max_width=self.logo_size,
            max_height=self.logo_size
        )

        return logo

    def _ensure_logo_installed(self) -> None:
        """
        Copy the bundled nfl_draft_logo.png to the core assets directory if it is not
        already present.  This runs on every startup so the logo is available
        after a fresh plugin install or update.
        """
        target = Path("assets/sports/nfl_logos/nfl_draft_logo.png")
        if target.exists():
            return  # Already installed, nothing to do

        # The logo ships alongside this manager.py file
        source = Path(__file__).parent / "nfl_draft_logo.png"
        if not source.exists():
            self.logger.warning(
                f"Bundled NFL Draft logo not found at {source}; "
                "logo will be unavailable until placed manually"
            )
            return

        try:
            import shutil
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(source), str(target))
            self.logger.info(f"Installed NFL Draft logo to {target}")
        except Exception as e:
            self.logger.error(f"Failed to install NFL Draft logo: {e}")

    def _load_nfl_draft_logo(self) -> Optional[Image.Image]:
        """
        Load the NFL Draft logo and return it as a display_height-tall canvas,
        ready to be prepended to the scroll content as the first item.

        Transparent borders are auto-cropped before resizing so that the visible
        logo fills as much vertical space as possible on the display.
        """
        logo_path = Path("assets/sports/nfl_logos/nfl_draft_logo.png")
        if not logo_path.exists():
            self.logger.warning(f"NFL Draft logo not found at {logo_path}")
            return None

        try:
            raw = Image.open(logo_path)
            if raw.mode != 'RGBA':
                raw = raw.convert('RGBA')

            # Crop away transparent borders so only the logo content remains.
            # Without this, a large transparent canvas (e.g. 1000×400 for a
            # logo that only fills ~380×390) would cause the resize to produce
            # a tiny result.
            bbox = raw.getbbox()
            if bbox:
                raw = raw.crop(bbox)

            # Resize to fit within display bounds while preserving aspect ratio
            raw.thumbnail(
                (self.display_width // 2, self.display_height),
                Image.Resampling.LANCZOS
            )

            # Wrap in a full display_height canvas so it composites cleanly
            canvas = Image.new('RGB', (raw.width, self.display_height), (0, 0, 0))
            y = (self.display_height - raw.height) // 2
            canvas.paste(raw, (0, y), raw)

            self.logger.debug(f"Loaded NFL Draft logo ({raw.width}x{raw.height})")
            return canvas

        except Exception as e:
            self.logger.error(f"Error loading NFL Draft logo: {e}")
            return None

    def update(self) -> None:
        """
        Fetch/update draft data from ESPN API.

        Called based on update_interval in manifest.
        Implements dual-mode logic:
        - During live draft: refresh every 10 minutes, show current round only
        - Off-season: daily refresh, show projected picks for configured rounds
        """
        current_time = time.time()

        # Determine refresh interval based on mode
        refresh_interval = self.live_refresh_interval if self.is_draft_live else self.projection_refresh_interval

        # Check if refresh is needed
        if self.last_update_time is not None and current_time - self.last_update_time < refresh_interval:
            return

        self.logger.info(f"Updating NFL Draft data (live={self.is_draft_live}, year={self.draft_year}, simulate={self.simulate_live})")

        try:
            if self.simulate_live:
                # Simulation mode: fetch real picks from core API for a completed draft
                # year and display them as-is (all configured rounds, no live filtering)
                self.draft_status = "simulate"
                self.draft_picks = self._fetch_historical_picks()
            else:
                # Normal mode: fetch from site API (live or projected picks)
                # also updates self.is_draft_live and self.current_round from API response
                self.draft_picks = self._fetch_draft_picks()

            # Sort by pick number
            self.draft_picks.sort(key=lambda x: x.get("pick_number", 0))

            # Mark the "on the clock" pick — first TBD pick in the current round
            # during a real live draft (not simulation, which has all picks filled in)
            for pick in self.draft_picks:
                pick.pop("on_clock", None)  # clear any stale flag
            if self.is_draft_live and not self.simulate_live:
                for pick in self.draft_picks:
                    if (pick.get("player_name") == "TBD"
                            and pick.get("round") == self.current_round):
                        pick["on_clock"] = True
                        break

            # Create scroll image
            self._create_draft_scroll_image()

            self.last_update_time = current_time
            self.logger.info(f"Loaded {len(self.draft_picks)} draft picks")

        except Exception as e:
            self.logger.error(f"Error updating draft data: {e}", exc_info=True)

    def display(self, force_clear: bool = False) -> None:
        """
        Render the draft picks to the LED matrix.

        Uses ScrollHelper to create smooth horizontal scrolling.

        Args:
            force_clear: If True, clear display before rendering
        """
        if force_clear:
            self.display_manager.clear()

        if not self.draft_picks:
            self._display_no_data()
            return

        try:
            # Update scroll position
            self.scroll_helper.update_scroll_position()

            # Get visible portion
            visible_image = self.scroll_helper.get_visible_portion()

            if visible_image:
                # Set image to display manager
                self.display_manager.image = visible_image
                self.display_manager.update_display()

        except Exception as e:
            self.logger.error(f"Error displaying draft: {e}")
            self._display_error()

    def _display_no_data(self) -> None:
        """Display a no data message."""
        img = Image.new('RGB', (self.display_width, self.display_height), (0, 0, 0))
        draw = ImageDraw.Draw(img)

        message = "No Draft Data"
        try:
            text_width = draw.textlength(message, font=self.detail_font)
        except Exception:
            bbox = draw.textbbox((0, 0), message, font=self.detail_font)
            text_width = bbox[2] - bbox[0]

        x = (self.display_width - text_width) // 2
        y = (self.display_height - self.detail_font_size) // 2

        draw.text((x, y), message, font=self.detail_font, fill=(150, 150, 150))

        self.display_manager.image = img
        self.display_manager.update_display()

    def _display_error(self) -> None:
        """Display an error message."""
        img = Image.new('RGB', (self.display_width, self.display_height), (50, 0, 0))
        draw = ImageDraw.Draw(img)

        message = "Error"
        try:
            text_width = draw.textlength(message, font=self.detail_font)
        except Exception:
            bbox = draw.textbbox((0, 0), message, font=self.detail_font)
            text_width = bbox[2] - bbox[0]

        x = (self.display_width - text_width) // 2
        y = (self.display_height - self.detail_font_size) // 2

        draw.text((x, y), message, font=self.detail_font, fill=(255, 100, 100))

        self.display_manager.image = img
        self.display_manager.update_display()

    def supports_dynamic_duration(self) -> bool:
        """Enable dynamic duration based on scroll completion."""
        return self.dynamic_duration_enabled

    def is_cycle_complete(self) -> bool:
        """Check if scroll cycle is complete."""
        return self.scroll_helper.is_scroll_complete()

    def reset_cycle_state(self) -> None:
        """Reset scroll state for new cycle."""
        self.scroll_helper.reset_scroll()

    def get_display_duration(self) -> float:
        """Get display duration, using dynamic duration from scroll helper."""
        if self.supports_dynamic_duration():
            return float(self.scroll_helper.get_dynamic_duration())
        return self.config.get('display_duration', 60.0)

    # -------------------------------------------------------------------------
    # Vegas scroll mode support
    # -------------------------------------------------------------------------

    def get_vegas_content_type(self) -> str:
        """Report as multi-item content so Vegas uses SCROLL mode by default."""
        return 'multi'

    def get_vegas_content(self) -> Optional[List[Image.Image]]:
        """
        Return one image per draft pick for Vegas scroll mode.

        Vegas composes these individually into the continuous scroll stream,
        giving smoother integration than handing it the pre-built scroll image.
        Returns None if no picks are loaded yet.
        """
        if not self.draft_picks:
            return None

        images = []

        if self.nfl_draft_logo:
            images.append(self.nfl_draft_logo)

        if self.is_draft_live:
            display_round, round_picks = self._get_display_round()
            images.append(self._create_round_label_item(display_round))

            for pick in self._get_favorite_team_picks():
                img = self._create_pick_item(pick)
                if img:
                    images.append(img)

            for pick in round_picks:
                img = self._create_pick_item(pick)
                if img:
                    images.append(img)

        elif self.simulate_live:
            picks_to_display = [p for p in self.draft_picks if p["round"] in self.rounds_to_display]

            for pick in self._get_favorite_team_picks():
                img = self._create_pick_item(pick)
                if img:
                    images.append(img)

            for pick in picks_to_display:
                img = self._create_pick_item(pick)
                if img:
                    images.append(img)

        else:
            for pick in [p for p in self.draft_picks if p["round"] in self.rounds_to_display]:
                img = self._create_pick_item(pick)
                if img:
                    images.append(img)

        return images if images else None

    def has_live_priority(self) -> bool:
        """Check if live priority is enabled."""
        return self.config.get("live_priority", False)

    def has_live_content(self) -> bool:
        """Check if draft is currently live."""
        return self.is_draft_live and self.draft_status == "live"

    def get_live_modes(self) -> List[str]:
        """Return display modes for live content."""
        return ["nfl_draft"]

    def validate_config(self) -> bool:
        """Validate plugin configuration."""
        if not super().validate_config():
            return False

        # Validate rounds format
        rounds_str = self.config.get("rounds", "1,2,3")
        if rounds_str.lower() != "all":
            try:
                rounds = [int(r.strip()) for r in rounds_str.split(",")]
                if not all(1 <= r <= 7 for r in rounds):
                    self.logger.error("Rounds must be between 1 and 7")
                    return False
            except (ValueError, AttributeError):
                self.logger.error("Invalid rounds format. Use comma-separated numbers or 'all'")
                return False

        return True

    def get_info(self) -> Dict[str, Any]:
        """Return plugin info for web UI."""
        info = super().get_info()
        info.update({
            'draft_year': self.draft_year,
            'is_live': self.is_draft_live,
            'draft_status': self.draft_status,
            'current_round': self.current_round,
            'picks_loaded': len(self.draft_picks),
            'rounds_configured': self.rounds_to_display
        })
        return info

    def cleanup(self) -> None:
        """Cleanup resources."""
        if hasattr(self, 'scroll_helper'):
            self.scroll_helper.clear_cache()
        if hasattr(self, 'logo_helper'):
            self.logo_helper.clear_cache()
        super().cleanup()

    def on_config_change(self, new_config: Dict[str, Any]) -> None:
        """Handle configuration changes."""
        super().on_config_change(new_config)
        self._load_config()
        self.player_name_font = self._load_font(self.player_name_font_size)
        self.detail_font = self._load_font(self.detail_font_size)

        # Force data refresh on config change
        self.last_update_time = None
