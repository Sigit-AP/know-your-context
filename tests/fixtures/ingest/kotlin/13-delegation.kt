// Interface delegation with by.
package demo

interface Logger {
    fun log(msg: String)
}

class ConsoleLogger : Logger {
    override fun log(msg: String) {
        println(msg)
    }
}

class Service(logger: Logger) : Logger by logger {
    fun work() {
        log("working")
    }
}
