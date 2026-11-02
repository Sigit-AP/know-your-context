;;; defvar/defcustom module state.
(defvar demo-counter 0
  "Global counter.")

(defcustom demo-limit 100
  "Maximum retries."
  :type 'integer
  :group 'demo)

(defun demo-next ()
  "Increment and return `demo-counter'."
  (setq demo-counter (1+ demo-counter)))
