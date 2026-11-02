(require 'cl-lib)

(defun greet (who)
  (helper who))

(defun helper (x)
  (upcase x))
