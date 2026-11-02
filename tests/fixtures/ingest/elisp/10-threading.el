;;; Threading macros (subr-x) in real code.
(require 'subr-x)

(defun demo-sluggify (text)
  "Slugify TEXT."
  (thread-first text
                (downcase)
                (replace-regexp-in-string "[^a-z0-9]+" "-")
                (string-trim)))

(defun demo-count-words (text)
  "Count words in TEXT."
  (thread-last text
               (split-string)
               (length)))
