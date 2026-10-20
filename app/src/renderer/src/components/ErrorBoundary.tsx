import { Component, type ReactNode } from 'react'
import { color, type } from '../theme'

interface Props {
  // What broke, in the message ("the BOM", "this chat").
  label: string
  children: ReactNode
}

interface State {
  error: Error | null
}

// A render error in one pane shows a notice there instead of blanking the whole
// window (React unmounts everything above an uncaught render error).
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: { componentStack?: string | null }): void {
    console.error(`[ui] ${this.props.label} failed to render:`, error, info.componentStack)
  }

  render(): ReactNode {
    const { error } = this.state
    if (!error) return this.props.children
    return (
      <div role="alert" style={{ padding: 16, color: color.inkSecondary, fontSize: type.size.sm }}>
        <p style={{ margin: '0 0 8px', color: color.inkPrimary }}>Something went wrong showing {this.props.label}.</p>
        <p style={{ margin: '0 0 12px', color: color.inkMuted, fontSize: type.size.xs, wordBreak: 'break-word' }}>
          {error.message || String(error)}
        </p>
        <button onClick={() => this.setState({ error: null })}>Try again</button>
      </div>
    )
  }
}
