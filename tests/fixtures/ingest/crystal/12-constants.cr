# Constants and class variables.
MAX_RETRIES = 3
DEFAULTS = {"timeout" => 30}

class Registry
  @@count = 0

  def self.bump : Int32
    @@count += 1
  end
end
