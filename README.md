# NFL Draft Plugin for LEDMatrix

Displays projected and live NFL draft picks from ESPN on your LED matrix display.

## Features

- **Projected Draft Picks**: Shows mock draft / projected picks during the off-season
- **Live Draft Tracking**: Automatically switches to live mode during the NFL Draft event
- **Team Logos**: Displays NFL team logos from core LEDMatrix assets
- **Scrolling Display**: Smooth horizontal scrolling through draft picks
- **Configurable Rounds**: Display specific rounds (1, 2, 3) or all rounds
- **Customizable Appearance**: Configure fonts, colors, and scroll speed

## Installation

### Option 1: Clone to plugin-repos

```bash
cd /path/to/LEDMatrix/plugin-repos
git clone https://github.com/your-username/ledmatrix-nfl-draft.git nfl-draft
```

### Option 2: Clone to plugins directory

```bash
cd /path/to/LEDMatrix/plugins
git clone https://github.com/your-username/ledmatrix-nfl-draft.git nfl-draft
```

## Configuration

Add the following to your `config/config.json`:

```json
{
  "nfl-draft": {
    "enabled": true,
    "display_duration": 60,
    "rounds": "1,2,3",
    "font": "4x6-font.ttf",
    "font_size": 6,
    "player_name_color": { "r": 255, "g": 255, "b": 255 },
    "scroll_speed": 30,
    "show_position": true,
    "logo_size": 20
  }
}
```

### Configuration Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `enabled` | boolean | `true` | Enable/disable the plugin |
| `display_duration` | number | `60` | Display duration in seconds |
| `rounds` | string | `"1,2,3"` | Rounds to display: comma-separated (e.g., "1,2,3"), single round (e.g., "1"), or "all" |
| `font` | string | `"4x6-font.ttf"` | Font file from assets/fonts/ |
| `font_size` | integer | `6` | Font size (4-12) |
| `player_name_color` | object | `{r:255,g:255,b:255}` | RGB color for player names |
| `pick_number_color` | object | `{r:255,g:255,b:255}` | RGB color for pick numbers |
| `scroll_speed` | number | `30` | Scroll speed in pixels per second |
| `live_refresh_interval` | integer | `600` | Refresh interval during live draft (seconds) |
| `projection_refresh_interval` | integer | `86400` | Refresh interval for projections (seconds) |
| `draft_year` | integer | `0` | Draft year (0 = auto-detect) |
| `show_position` | boolean | `true` | Show player position |
| `show_college` | boolean | `false` | Show player college |
| `logo_size` | integer | `20` | Team logo size in pixels |
| `item_gap` | integer | `32` | Gap between draft picks |
| `live_priority` | boolean | `false` | Enable live priority during draft |

## Display Layout

```
[LOGO]  Player Name, POS  #1
```

- **Left**: NFL team logo
- **Center**: Player name and position (configurable)
- **Right**: Pick number in white

## Dual-Mode Operation

### Pre-Draft Mode (Default)
- Shows projected/mock draft picks
- Refreshes daily (configurable)
- Displays configured rounds (default: 1, 2, 3)

### Live Draft Mode
- Automatically detected when NFL Draft is live
- Refreshes every 10 minutes (configurable)
- Shows only the current round being drafted

## Data Source

This plugin uses the ESPN public API for draft data:
- No API key required
- Provides projected picks and live draft data
- Automatically handles API rate limiting

## Requirements

- LEDMatrix v2.0.0 or higher
- Minimum display size: 64x32 pixels
- Python 3.9+

## License

MIT License

## Contributing

Contributions are welcome! Please open an issue or pull request.
