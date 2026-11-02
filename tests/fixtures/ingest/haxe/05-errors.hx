// try/catch, throw and typed catches.
class ConfigError {
    public var key:String;

    public function new(key:String) {
        this.key = key;
    }

    public function toString():String {
        return "missing config key: " + key;
    }
}

class Config {
    public static function read(map:Map<String, String>, key:String):String {
        try {
            if (!map.exists(key)) {
                throw new ConfigError(key);
            }
            return map.get(key);
        } catch (e:ConfigError) {
            throw e;
        } catch (e:Dynamic) {
            return "";
        }
    }
}
