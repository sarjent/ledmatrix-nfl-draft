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

import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

from src.plugin_system.base_plugin import BasePlugin
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
        self.font_name = self.config.get("font", "4x6-font.ttf")
        self.player_name_font_size = self.config.get("player_name_font_size", 12)
        self.detail_font_size = self.config.get("detail_font_size", 6)

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
        self.show_college = self.config.get("show_college", False)
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

        # Get top prospects for mock draft (pre-draft mode)
        prospects = []
        current_data = data.get("current", {})
        best_available = current_data.get("bestAvailablePicks", [])

        if best_available:
            # Sort by overall rank
            for prospect in best_available:
                overall_rank = 999
                for attr in prospect.get("attributes", []):
                    if attr.get("name") == "overall":
                        try:
                            overall_rank = int(attr.get("displayValue", 999))
                        except (ValueError, TypeError):
                            pass
                prospect["_overall_rank"] = overall_rank
            prospects = sorted(best_available, key=lambda x: x.get("_overall_rank", 999))

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

                # Get position
                position = prospect.get("position", {})
                if isinstance(position, dict):
                    pos_id = position.get("id")
                    # Map position ID to abbreviation from positions list
                    for pos in data.get("positions", []):
                        if str(pos.get("id")) == str(pos_id):
                            pick_data["position"] = pos.get("abbreviation", "")
                            break

                # Get college from team (prospect's college team)
                prospect_team = prospect.get("team", {})
                if prospect_team:
                    pick_data["college"] = prospect_team.get("shortDisplayName", prospect_team.get("name", ""))

            # For live/post draft, use athlete data if available
            elif raw_pick.get("athlete"):
                athlete = raw_pick["athlete"]
                pick_data["player_name"] = athlete.get("displayName", "TBD")
                position = athlete.get("position", {})
                if isinstance(position, dict):
                    pick_data["position"] = position.get("abbreviation", "")

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

    def _create_draft_scroll_image(self) -> None:
        """Create scrolling image with all draft picks."""
        content_items = []

        # Filter picks by configured rounds
        picks_to_display = [p for p in self.draft_picks if p["round"] in self.rounds_to_display]

        # During live draft, show only current round
        if self.is_draft_live:
            picks_to_display = [p for p in self.draft_picks if p["round"] == self.current_round]

        for pick in picks_to_display:
            item_image = self._create_pick_item(pick)
            if item_image:
                content_items.append(item_image)

        if content_items:
            # Create the scrolling image using ScrollHelper
            self.scroll_helper.create_scrolling_image(
                content_items,
                item_gap=self.item_gap,
                element_gap=8
            )

            self.logger.info(f"Created scroll image with {len(content_items)} picks")
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
        player_name = pick.get("player_name", "TBD")

        # Build detail line: POS  #PICK  (School)
        detail_parts = []

        # Position
        if self.show_position and pick.get("position"):
            detail_parts.append(pick["position"])

        # Pick number
        pick_number = pick.get("pick_number", 0)
        detail_parts.append(f"#{pick_number}")

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
        draw.text((text_start_x, player_name_y), player_name, font=self.player_name_font, fill=self.player_color)

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

        self.logger.info(f"Updating NFL Draft data (live={self.is_draft_live}, year={self.draft_year})")

        try:
            # Fetch all draft picks - _fetch_draft_picks handles filtering by rounds
            # and also updates self.is_draft_live and self.current_round from API response
            self.draft_picks = self._fetch_draft_picks()

            # Sort by pick number
            self.draft_picks.sort(key=lambda x: x.get("pick_number", 0))

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
