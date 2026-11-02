// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract Ledger {
    event Transfer(address indexed from, address indexed to, uint256 amount);
    event Paused(address by);

    function record(address to, uint256 amount) external {
        emit Transfer(msg.sender, to, amount);
    }

    function pause() external {
        emit Paused(msg.sender);
    }
}
