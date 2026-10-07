// Unified inline SVG Icons library - Zero emojis used anywhere
import React from 'react'

export function Icon({ name, size = 16, className = '', color = 'currentColor', style = {} }) {
  const props = {
    width: size,
    height: size,
    viewBox: '0 0 24 24',
    fill: 'none',
    stroke: color,
    strokeWidth: 2,
    strokeLinecap: 'round',
    strokeLinejoin: 'round',
    className: `df-icon ${className}`,
    style: { display: 'inline-block', verticalAlign: 'middle', flexShrink: 0, ...style },
  }

  switch (name) {
    // --- Domain & Entities ---
    case 'plant':
    case 'factory':
      // Factory with chimney + milk drop
      return (
        <svg {...props}>
          <path d="M2 20h20M4 20V10l5 4V10l5 4V6h4v14" />
          <path d="M16 11c0 1.5-1 2.5-1 2.5s-1-1-1-2.5a1 1 0 0 1 2 0z" fill={color} />
        </svg>
      )

    case 'hub':
    case 'milk-churn':
      // Milk churn / can glyph
      return (
        <svg {...props}>
          <path d="M7 3h10M8 3v3h8V3M6 9h12M5 9v11a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V9M9 13h6M9 17h6" />
        </svg>
      )

    case 'truck':
    case 'tanker':
      // Milk tanker truck
      return (
        <svg {...props}>
          <rect x="1" y="8" width="13" height="8" rx="2" />
          <path d="M14 10h4l3 3v3h-7v-6z" />
          <circle cx="5.5" cy="18.5" r="2.5" />
          <circle cx="17.5" cy="18.5" r="2.5" />
          <path d="M4 12h4" />
        </svg>
      )

    case 'milk-drop':
      return (
        <svg {...props}>
          <path d="M12 2.69l5.66 5.66a8 8 0 1 1-11.31 0z" fill={color} fillOpacity="0.2" />
        </svg>
      )

    // --- Disruptions ---
    case 'rain':
      // Cloud with rain drops
      return (
        <svg {...props}>
          <path d="M4 14.899A7 7 0 1 1 15.71 8h1.79a4.5 4.5 0 0 1 2.5 8.242" />
          <path d="M8 19v2M8 13v2M12 21v2M12 15v2M16 19v2M16 13v2" strokeWidth="2.5" />
        </svg>
      )

    case 'traffic':
      // Traffic light / congestion icon
      return (
        <svg {...props}>
          <rect x="7" y="2" width="10" height="20" rx="4" />
          <circle cx="12" cy="6" r="1.5" fill={color} />
          <circle cx="12" cy="12" r="1.5" fill={color} />
          <circle cx="12" cy="18" r="1.5" fill={color} />
        </svg>
      )

    case 'roadblock':
      // Road closure barrier (striped gate)
      return (
        <svg {...props}>
          <circle cx="12" cy="12" r="10" />
          <path d="M4.93 4.93l14.14 14.14" strokeWidth="2.5" />
        </svg>
      )

    case 'area-closure':
      // Warning perimeter / polygon barrier
      return (
        <svg {...props}>
          <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
          <line x1="12" y1="9" x2="12" y2="13" />
          <line x1="12" y1="17" x2="12.01" y2="17" />
        </svg>
      )

    // --- App UI & Controls ---
    case 'play':
      return (
        <svg {...props}>
          <polygon points="5 3 19 12 5 21 5 3" fill={color} />
        </svg>
      )

    case 'pause':
      return (
        <svg {...props}>
          <rect x="6" y="4" width="4" height="16" fill={color} />
          <rect x="14" y="4" width="4" height="16" fill={color} />
        </svg>
      )

    case 'restart':
    case 'refresh':
      return (
        <svg {...props}>
          <path d="M21.5 2v6h-6M2.5 22v-6h6" />
          <path d="M20 11.5a8.5 8.5 0 0 0-14.7-4.3L2.5 16M21.5 8l-2.8 2.8A8.5 8.5 0 0 1 4 12.5" />
        </svg>
      )

    case 'sun':
      return (
        <svg {...props}>
          <circle cx="12" cy="12" r="5" />
          <line x1="12" y1="1" x2="12" y2="3" />
          <line x1="12" y1="21" x2="12" y2="23" />
          <line x1="4.22" y1="4.22" x2="5.64" y2="5.64" />
          <line x1="18.36" y1="18.36" x2="19.78" y2="19.78" />
          <line x1="1" y1="12" x2="3" y2="12" />
          <line x1="21" y1="12" x2="23" y2="12" />
          <line x1="4.22" y1="19.78" x2="5.64" y2="18.36" />
          <line x1="18.36" y1="5.64" x2="19.78" y2="4.22" />
        </svg>
      )

    case 'moon':
      return (
        <svg {...props}>
          <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
        </svg>
      )

    case 'help':
    case 'info':
      return (
        <svg {...props}>
          <circle cx="12" cy="12" r="10" />
          <line x1="12" y1="16" x2="12" y2="12" />
          <line x1="12" y1="8" x2="12.01" y2="8" />
        </svg>
      )

    case 'check':
      return (
        <svg {...props}>
          <polyline points="20 6 9 17 4 12" />
        </svg>
      )

    case 'close':
    case 'x':
      return (
        <svg {...props}>
          <line x1="18" y1="6" x2="6" y2="18" />
          <line x1="6" y1="6" x2="18" y2="18" />
        </svg>
      )

    case 'chevron-down':
      return (
        <svg {...props}>
          <polyline points="6 9 12 15 18 9" />
        </svg>
      )

    case 'chevron-right':
      return (
        <svg {...props}>
          <polyline points="9 18 15 12 9 6" />
        </svg>
      )

    case 'chevron-left':
      return (
        <svg {...props}>
          <polyline points="15 18 9 12 15 6" />
        </svg>
      )

    case 'chevron-up':
      return (
        <svg {...props}>
          <polyline points="18 15 12 9 6 15" />
        </svg>
      )

    case 'sidebar-left':
      return (
        <svg {...props}>
          <rect x="3" y="3" width="18" height="18" rx="2" />
          <line x1="9" y1="3" x2="9" y2="21" />
        </svg>
      )

    case 'sidebar-right':
      return (
        <svg {...props}>
          <rect x="3" y="3" width="18" height="18" rx="2" />
          <line x1="15" y1="3" x2="15" y2="21" />
        </svg>
      )

    case 'phone':
      return (
        <svg {...props}>
          <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z" />
        </svg>
      )

    case 'edit':
      return (
        <svg {...props}>
          <path d="M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z" />
        </svg>
      )

    case 'priority':
    case 'flag':
      return (
        <svg {...props}>
          <path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z" />
          <line x1="4" y1="22" x2="4" y2="15" />
        </svg>
      )

    case 'alert':
    case 'warning':
      return (
        <svg {...props}>
          <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
          <line x1="12" y1="9" x2="12" y2="13" />
          <line x1="12" y1="17" x2="12.01" y2="17" />
        </svg>
      )

    case 'clock':
      return (
        <svg {...props}>
          <circle cx="12" cy="12" r="10" />
          <polyline points="12 6 12 12 16 14" />
        </svg>
      )

    case 'map-pin':
      return (
        <svg {...props}>
          <path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z" />
          <circle cx="12" cy="10" r="3" />
        </svg>
      )

    case 'fuel':
      return (
        <svg {...props}>
          <path d="M3 22h12M4 9h10M4 22V4a2 2 0 0 1 2-2h6a2 2 0 0 1 2 2v18" />
          <path d="M14 13h2a2 2 0 0 1 2 2v2a2 2 0 0 0 2 2h0a2 2 0 0 0 2-2V9.83a2 2 0 0 0-.59-1.42L19 6" />
        </svg>
      )

    case 'layers':
      return (
        <svg {...props}>
          <polygon points="12 2 2 7 12 12 22 7 12 2" />
          <polyline points="2 17 12 22 22 17" />
          <polyline points="2 12 12 17 22 12" />
        </svg>
      )

    case 'user':
      return (
        <svg {...props}>
          <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
          <circle cx="12" cy="7" r="4" />
        </svg>
      )

    case 'settings':
      return (
        <svg {...props}>
          <circle cx="12" cy="12" r="3" />
          <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z" />
        </svg>
      )

    default:
      return (
        <svg {...props}>
          <circle cx="12" cy="12" r="10" />
        </svg>
      )
  }
}
