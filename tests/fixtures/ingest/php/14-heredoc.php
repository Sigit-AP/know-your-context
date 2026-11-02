<?php
// Heredoc/nowdoc and string interpolation.
class Render
{
    public function page(string $title): string
    {
        return <<<HTML
            <html>
              <body>$title</body>
            </html>
            HTML;
    }

    public function raw(): string
    {
        return <<<'EOT'
            no $interpolation here
            EOT;
    }
}
