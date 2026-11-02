// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract Access {
    address public owner;
    bool public locked;

    constructor() {
        owner = msg.sender;
    }

    modifier onlyOwner() {
        require(msg.sender == owner, "not owner");
        _;
    }

    modifier whenUnlocked() {
        require(!locked, "locked");
        _;
    }

    function configure() external onlyOwner whenUnlocked {
        locked = true;
    }
}
