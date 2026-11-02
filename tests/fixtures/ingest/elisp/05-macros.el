;;; defmacro definitions.
(defmacro demo-when-let (binding &rest body)
  "Evaluate BODY when BINDING is non-nil."
  `(let ((,(car binding) ,(cadr binding)))
     (when ,(car binding)
       ,@body)))

(defmacro demo-with-log (label &rest body)
  "Log LABEL around BODY."
  `(progn
     (message "enter %s" ,label)
     (prog1 (progn ,@body)
       (message "exit %s" ,label))))

(defun demo-run-with-log (label)
  "Run a body under `demo-with-log'."
  (demo-with-log label
    (message "body")))
