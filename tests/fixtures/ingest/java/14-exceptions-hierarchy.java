// Exception hierarchies and multi-catch.
package demo;

import java.io.IOException;

abstract class AppException extends Exception {
    AppException(String message) {
        super(message);
    }
}

class NotFound extends AppException {
    NotFound(String what) {
        super("not found: " + what);
    }
}

class Store {
    String load(String key) throws NotFound {
        if (key.isEmpty()) {
            throw new NotFound(key);
        }
        return key;
    }

    String loadSafe(String key) {
        try {
            return load(key);
        } catch (NotFound e) {
            return "";
        }
    }
}
