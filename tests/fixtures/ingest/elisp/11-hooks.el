;;; Hook registration and minor modes.
(defun demo-on-save ()
  "Hook run before save."
  (message "saving"))

(add-hook 'before-save-hook #'demo-on-save)

(define-minor-mode demo-mode
  "A demo minor mode."
  :lighter " Demo")

(defun demo-enable ()
  "Enable demo mode."
  (demo-mode 1))
