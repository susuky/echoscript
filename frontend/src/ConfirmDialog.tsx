import { useEffect, useId, useRef } from 'react';
import { useLocale } from './i18n';

export default function ConfirmDialog({
  title,
  message,
  confirmLabel,
  cancelLabel,
  onConfirm,
  onCancel,
}: {
  title: string;
  message: string;
  confirmLabel: string;
  cancelLabel: string;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const { t } = useLocale();
  const dialog = useRef<HTMLDialogElement>(null);
  const id = useId();

  useEffect(() => {
    const element = dialog.current!;
    element.showModal();
    return () => element.close();
  }, []);

  return (
    <dialog
      ref={dialog}
      className="confirm-dialog"
      aria-labelledby={`${id}-title`}
      aria-describedby={`${id}-message`}
      onCancel={onCancel}
    >
      <h2 id={`${id}-title`}>{t(title)}</h2>
      <p id={`${id}-message`}>{t(message)}</p>
      <div className="confirmation-actions">
        <button className="button" type="button" autoFocus onClick={onCancel}>
          {t(cancelLabel)}
        </button>
        <button className="button danger" type="button" onClick={onConfirm}>
          {t(confirmLabel)}
        </button>
      </div>
    </dialog>
  );
}
