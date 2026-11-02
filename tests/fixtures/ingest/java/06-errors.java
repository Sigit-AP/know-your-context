// Checked/unchecked exceptions and try-with-resources.
package demo;

import java.io.BufferedReader;
import java.io.IOException;

class ConfigException extends Exception {
    ConfigException(String key) {
        super("missing config key: " + key);
    }
}

class Config {
    String read(java.util.Map<String, String> map, String key) throws ConfigException {
        String value = map.get(key);
        if (value == null) {
            throw new ConfigException(key);
        }
        return value;
    }

    String firstLine(BufferedReader reader) {
        try (BufferedReader r = reader) {
            return r.readLine();
        } catch (IOException e) {
            return "";
        } finally {
            // closed
        }
    }
}
