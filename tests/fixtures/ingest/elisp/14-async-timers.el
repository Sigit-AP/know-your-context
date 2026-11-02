;;; Timers as async callbacks.
(defvar demo-timer nil
  "Active timer.")

(defun demo-schedule (seconds fn)
  "Run FN after a delay, storing the timer."
  (setq demo-timer
        (run-at-time 1 nil fn)))

(defun demo-cancel ()
  "Cancel the active timer."
  (when demo-timer
    (cancel-timer demo-timer)
    (setq demo-timer nil)))
