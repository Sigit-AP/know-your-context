;;; condition-case and signal.
(define-error 'demo-config-error "Missing config key")

(defun demo-read-config (map key)
  "Read KEY from MAP or signal."
  (let ((value (cdr (assoc key map))))
    (unless value
      (signal 'demo-config-error (list key)))
    value))

(defun demo-read-safe (map key)
  "Read KEY from MAP, nil on failure."
  (condition-case nil
      (demo-read-config map key)
    (demo-config-error nil)))
