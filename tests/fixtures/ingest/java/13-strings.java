// Text blocks, switch expressions and pattern matching.
package demo;

class Render {
    String page(String title) {
        return """
               <html>
                 <body>%s</body>
               </html>
               """.formatted(title);
    }

    int classify(Level level) {
        return switch (level) {
            case LOW -> 1;
            case HIGH -> 2;
        };
    }

    String describe(Object value) {
        if (value instanceof String s) {
            return s.strip();
        }
        return "?";
    }
}
