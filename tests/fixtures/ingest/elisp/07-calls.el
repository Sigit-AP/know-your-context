;;; Calls parking unresolved refs.
(defun demo-helper (value)
  "Double VALUE."
  (* value 2))

(defun demo-run (seed)
  "Run the helper chain from SEED."
  (demo-helper (demo-helper seed)))
