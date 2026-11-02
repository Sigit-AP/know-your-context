# Constants, globals and class variables.
MAX_RETRIES = 3
$debug = false
DEFAULTS = { timeout: 30 }.freeze

class Registry
  @@count = 0

  def self.bump
    @@count += 1
  end
end
