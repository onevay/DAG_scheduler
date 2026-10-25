import { Component, type ErrorInfo, type ReactNode } from "react";

import { clearDraft } from "../draft/draft";
import { Button } from "../ui/controls";
import { PageHeader } from "../ui/PageHeader";
import styles from "./ErrorBoundary.module.css";

interface State {
  error: Error | null;
}

/**
 * Ошибка при отрисовке экрана — понятное сообщение и выход вместо белой страницы. Частые причины: испорченный
 * черновик (тогда помогает его сброс) и не загрузившийся кусок клиента после обновления (помогает перезагрузка).
 */
export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("screen failed to render", error, info.componentStack);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <div role="alert" className={styles.box}>
        <PageHeader title="Экран не открылся">
          Перезагрузите страницу. Если не поможет — сбросьте черновик: он хранится в браузере и мог
          испортиться.
        </PageHeader>
        <code className={styles.detail}>{error.message}</code>
        <div className={styles.actions}>
          <Button onClick={() => window.location.reload()}>Перезагрузить</Button>
          <Button
            variant="secondary"
            onClick={() => {
              clearDraft();
              window.location.reload();
            }}
          >
            Сбросить черновик
          </Button>
        </div>
      </div>
    );
  }
}
