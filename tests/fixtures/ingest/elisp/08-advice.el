;;; advice-add and interactive commands.
(defun demo-target ()
  "A function to advise."
  :target)

(defun demo-around (orig &rest args)
  "Around advice for `demo-target'."
  (apply orig (mapcar #'1+ args)))

(advice-add 'demo-target :around #'demo-around)

(defun demo-command ()
  "An interactive command."
  (interactive)
  (message "invoked"))
