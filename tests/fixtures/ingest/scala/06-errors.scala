// try/catch/finally, Try and custom exceptions.
package demo

import scala.util.{Failure, Success, Try}

class ConfigError(val key: String) extends Exception(s"missing config key: $key")

def readConfig(map: Map[String, String], key: String): String =
  try {
    map.getOrElse(key, throw new ConfigError(key))
  } catch {
    case e: ConfigError => throw ConfigError(s"re:${e.key}")
  } finally {
    map.size
  }

def safeRead(map: Map[String, String], key: String): Try[String] =
  Try(readConfig(map, key))
