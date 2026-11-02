// Exceptions: throw, try/catch, custom error type. */
#include <stdexcept>
#include <string>

class ConfigError : public std::runtime_error {
 public:
  explicit ConfigError(const std::string &key) : std::runtime_error("missing: " + key) {}
};

int parse_or_throw(const std::string &text) {
  try {
    return std::stoi(text);
  } catch (const std::invalid_argument &) {
    throw ConfigError(text);
  } catch (...) {
    throw;
  }
}
