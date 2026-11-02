;;; require and load forms.
(require 'cl-lib)
(require 'seq)
(require 'subr-x)

(defun demo-joined (items)
  "Join ITEMS with commas."
  (string-join items ","))

(provide 'demo-requires)
