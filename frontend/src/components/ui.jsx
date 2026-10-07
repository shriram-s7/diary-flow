// Control room UI components - Zero inline styles and zero hardcoded hex
import React, { useRef, useEffect } from 'react'
import { Icon } from './Icons'

export function Button({
  variant = 'secondary', // primary | secondary | success | warning | danger | ghost | outline
  size = 'md',          // sm | md | lg
  icon,
  iconOnly = false,
  className = '',
  disabled = false,
  children,
  onClick,
  type = 'button',
  title,
  ...props
}) {
  const classes = [
    'df-btn',
    `df-btn-${variant}`,
    `df-btn-${size}`,
    iconOnly ? 'df-btn-icon-only' : '',
    className,
  ].filter(Boolean).join(' ')

  return (
    <button
      type={type}
      className={classes}
      disabled={disabled}
      onClick={onClick}
      title={title}
      {...props}
    >
      {icon && <Icon name={icon} size={size === 'sm' ? 14 : size === 'lg' ? 18 : 16} />}
      {!iconOnly && children}
    </button>
  )
}

export function Tabs({ tabs, activeTab, onChange, className = '' }) {
  return (
    <div className={`df-tabs ${className}`}>
      {tabs.map((tab) => {
        const id = typeof tab === 'string' ? tab : tab.id
        const label = typeof tab === 'string' ? tab : tab.label
        const icon = tab.icon
        const badge = tab.badge
        const isActive = activeTab === id

        return (
          <button
            key={id}
            type="button"
            className={`df-tab-item ${isActive ? 'active' : ''}`}
            onClick={() => onChange(id)}
          >
            {icon && <Icon name={icon} size={14} />}
            <span>{label}</span>
            {badge != null && badge > 0 && <span className="df-tab-badge">{badge}</span>}
          </button>
        )
      })}
    </div>
  )
}

export function Chip({
  variant = 'neutral', // neutral | success | warning | danger | info | unreachable
  icon,
  className = '',
  children,
}) {
  return (
    <span className={`df-chip df-chip-${variant} ${className}`}>
      {icon && <Icon name={icon} size={12} />}
      <span>{children}</span>
    </span>
  )
}

export function Card({
  title,
  subtitle,
  icon,
  action,
  interactive = false,
  onClick,
  className = '',
  children,
}) {
  return (
    <div
      className={`df-card ${interactive ? 'interactive' : ''} ${className}`}
      onClick={interactive ? onClick : undefined}
    >
      {(title || action) && (
        <div className="df-card-header">
          <div className="df-card-title">
            {icon && <Icon name={icon} size={16} />}
            <span>{title}</span>
            {subtitle && <span className="df-card-subtitle">{subtitle}</span>}
          </div>
          {action && <div className="df-card-action">{action}</div>}
        </div>
      )}
      <div className="df-card-body">{children}</div>
    </div>
  )
}

export function Progress({
  value = 0,
  max = 100,
  label,
  valueText,
  variant, // success | warning | danger | truck index
  className = '',
}) {
  const fillRef = useRef(null)
  const pct = Math.max(0, Math.min(100, max > 0 ? (value / max) * 100 : 0))

  useEffect(() => {
    if (fillRef.current) {
      fillRef.current.style.width = `${pct}%`
    }
  }, [pct])

  return (
    <div className={`df-progress-container ${className}`}>
      {(label || valueText) && (
        <div className="df-progress-header">
          {label && <span>{label}</span>}
          {valueText && <span className="df-progress-text">{valueText}</span>}
        </div>
      )}
      <div className="df-progress-track">
        <div
          ref={fillRef}
          className={`df-progress-fill ${variant ? variant : ''}`}
        />
      </div>
    </div>
  )
}

export function Select({ value, onChange, options = [], className = '', ...props }) {
  return (
    <select
      className={`df-select ${className}`}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      {...props}
    >
      {options.map((opt) => (
        <option key={opt.value} value={opt.value}>
          {opt.label}
        </option>
      ))}
    </select>
  )
}

export function Slider({
  value,
  onChange,
  min = 0,
  max = 100,
  step = 1,
  label,
  valueDisplay,
  className = '',
}) {
  return (
    <div className={`df-slider-container ${className}`}>
      {(label || valueDisplay) && (
        <div className="df-slider-label-row">
          {label && <span>{label}</span>}
          {valueDisplay && <span className="df-slider-text">{valueDisplay}</span>}
        </div>
      )}
      <input
        type="range"
        className="df-slider"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
      />
    </div>
  )
}

export function Modal({
  title,
  icon,
  onClose,
  size = 'md', // sm | md | lg
  footer,
  children,
}) {
  return (
    <div className="df-modal-backdrop" onClick={onClose}>
      <div
        className={`df-modal df-modal-${size}`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="df-modal-header">
          <div className="df-modal-title">
            {icon && <Icon name={icon} size={20} />}
            <span>{title}</span>
          </div>
          <Button
            variant="ghost"
            size="sm"
            icon="close"
            iconOnly
            onClick={onClose}
            title="Close"
          />
        </div>
        <div className="df-modal-body">{children}</div>
        {footer && <div className="df-modal-footer">{footer}</div>}
      </div>
    </div>
  )
}

export function ToastStack({ toasts = [], onDismiss }) {
  if (!toasts.length) return null

  return (
    <div className="df-toast-stack">
      {toasts.slice(-3).map((toast) => (
        <div
          key={toast.id}
          className={`df-toast ${toast.type || 'info'}`}
          onClick={() => onDismiss?.(toast.id)}
        >
          <Icon
            name={
              toast.type === 'success'
                ? 'check'
                : toast.type === 'danger'
                ? 'alert'
                : toast.type === 'warning'
                ? 'warning'
                : 'info'
            }
            size={16}
          />
          <div className="df-toast-msg">{toast.message}</div>
          <span className="df-toast-close">
            <Icon name="close" size={14} />
          </span>
        </div>
      ))}
    </div>
  )
}

export function RiskBadge({ risk }) {
  if (!risk) return null
  const variant =
    risk === 'Low'
      ? 'success'
      : risk === 'Medium'
      ? 'warning'
      : risk === 'High'
      ? 'danger'
      : risk === 'Late' || risk === 'UNREACHABLE'
      ? 'unreachable'
      : 'neutral'

  return <Chip variant={variant}>{risk}</Chip>
}

export function ErrorText({ children }) {
  if (!children) return null
  return <div className="df-error-box">{children}</div>
}
