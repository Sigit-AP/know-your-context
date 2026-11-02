# Symbols, string interpolation and heredocs.
class Render
  def page(title)
    <<~HTML
      <html>
        <body>#{title}</body>
      </html>
    HTML
  end

  def describe
    %i[alpha beta gamma].map(&:to_s)
  end
end
