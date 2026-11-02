;;; Buffer manipulation functions.
(defun demo-current-line ()
  "Return the current line text."
  (buffer-substring-no-properties (line-beginning-position)
                                  (line-end-position)))

(defun demo-replace-all (from to)
  "Replace all occurrences of FROM with TO."
  (save-excursion
    (goto-char (point-min))
    (while (search-forward from nil t)
      (replace-match to))))

(defun demo-with-buffer (name fn)
  "Call FN in a fresh buffer NAME."
  (with-temp-buffer
    (rename-buffer name)
    (funcall fn)))
