"""

    Machine Learning Project Work: Tennis Table Tournament
    Group 2:
        Ciaravola Giosuè - g.ciaravola3#studenti.unisa.it
        Conato Christian - c.conato@studenti.unisa.it
        Del Gaudio Nunzio - n.delgaudio5@studenti.unisa.it
        Garofalo Mariachiara - m.garofalo38@studenti.unisa.it

    ---------------------------------------------------------------

    paddle_train.py

    File containing reinforcement learning for the two types of paddles,
    with alternating phases of episode recording and training.

"""

import sys
import os

# Import modules from parent directories
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.abspath(os.path.join(current_dir, os.pardir))
sys.path.append(parent_dir)

import torch
import math
from client import Client, DEFAULT_PORT
from utilities.action_space import ActionSpaceArm, ActionSpacePaddleSmash, ActionSpacePaddleDontWait
from server import get_neutral_joint_position
import numpy as np
from nets.arm_net import ArmModel
from utilities.trajectory import trajectory, max_height_point
from utilities.replay_memory import ReplayMemory, Transition
from utilities.noise import OrnsteinUhlenbeckActionNoise
from nets.ddpg import DDPG, hard_update
from utilities.reward_calculator import calculate_paddle_reward

# Set the device to GPU if available, otherwise use CPU
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print("Using: ", device)

# Hyperparameters
GAMMA = 0.99
TAU = 0.01
HIDDEN_SIZE_PADDLE = (200, 100, 50)
NUM_INPUTS_PADDLE = 6
TOTAL_EPOCH = 2
BATCH_S = 25
BATCH_DW = 25
REPLAY_SIZE_SMASH = 50
REPLAY_SIZE_DW = 50

# Action space and agent initialization for smash and don't wait
ACTION_SPACE_SMASH = ActionSpacePaddleSmash()
ACTION_SPACE_DONT_WAIT = ActionSpacePaddleDontWait()

smash_agent = DDPG(GAMMA,
                 TAU,
                 HIDDEN_SIZE_PADDLE,
                 NUM_INPUTS_PADDLE,
                 ACTION_SPACE_SMASH,
                 checkpoint_dir="saved_models_smash"
                 )
# smash_agent.load_checkpoint()

dont_wait_agent = DDPG(GAMMA,
                 TAU,
                 HIDDEN_SIZE_PADDLE,
                 NUM_INPUTS_PADDLE,
                 ACTION_SPACE_DONT_WAIT,
                 checkpoint_dir="saved_models_dont_wait"
                 )
# dont_wait_agent.load_checkpoint()


# Initialize OU-Noise for exploration
noise_stddev = 0.4

# Initialize OU-Noise for the exploration
ou_noise_smash = OrnsteinUhlenbeckActionNoise(mu=np.zeros(ACTION_SPACE_SMASH.shape),
                                            sigma=float(noise_stddev) * np.ones(ACTION_SPACE_SMASH.shape))

# Initialize OU-Noise for the exploration
ou_noise_dont_wait = OrnsteinUhlenbeckActionNoise(mu=np.zeros(ACTION_SPACE_DONT_WAIT.shape),
                                            sigma=float(noise_stddev) * np.ones(ACTION_SPACE_DONT_WAIT.shape))


# Replay memory for smash and don't wait agents
smash_memory = ReplayMemory(REPLAY_SIZE_SMASH)
dont_wait_memory = ReplayMemory(REPLAY_SIZE_DW)

# Arm model initialization
HIDDEN_SIZE_ARM = (100, 50)
ACTION_SPACE_ARM = ActionSpaceArm()
NUM_INPUTS_ARM = 2

arm_model = ArmModel(HIDDEN_SIZE_ARM, NUM_INPUTS_ARM, ACTION_SPACE_ARM).to(device)
arm_model.load_checkpoint()
arm_model.eval()


def run(cli):

    # Transition registered
    tr_dw = 0
    tr_s = 0

    # Model number
    model_number_s = 0
    model_number_dw = 0

    # Counter to do the hard update of target networks
    hard_s = 0
    hard_dw = 0

    # Flag to manage the game
    out = False
    wait_bounce_to_smash = False
    stance_chosen = False

    # input array for the two kind of models
    input_state_arm = np.zeros(NUM_INPUTS_ARM)
    input_state_paddle = np.zeros(NUM_INPUTS_PADDLE)

    action = get_neutral_joint_position()
    prev_state = cli.get_state()

    while True:
        if tr_s >= REPLAY_SIZE_SMASH:
            tr_s = 0

        if tr_dw >= REPLAY_SIZE_DW:
            tr_dw = 0

        prev_state = cli.get_state()

        while tr_s < REPLAY_SIZE_SMASH and tr_dw < REPLAY_SIZE_DW:

            # Each state:
            # - Read the state;
            state = cli.get_state()

            # Game start or the ball is going to the opponent (positive ball-y-velocity):
            # - Reset all the flag to manage the game;
            # - Take a good position to wait the activation
            if (not prev_state[28] and state[28]) or (state[21] > 0 and prev_state[18] > 1.2):

                x = 0
                y = 0.8
                z = 0.5

                input_state_arm[0] = y
                input_state_arm[1] = z

                input_state_arm = torch.Tensor(input_state_arm)

                arm_action = arm_model(input_state_arm)
                action[0] = arm_action[0]
                action[1] = x
                action[3] = arm_action[1]
                action[5] = arm_action[2]
                action[7] = arm_action[3]
                action[9] = 1.1
                action[10] = math.pi/2

                out = False
                wait_bounce_to_smash = False
                stance_chosen = False

            # Activation:
            # - If the ball is coming to us (negative ball-y-velocity);
            # - And the game is playing;
            # - And the ball is not going out
            # - And the ball (pen) is on the table (negative ball-z-position)
            if state[21] < 0 and state[28] and not out and state[19] > 0:
                # In the state in which the opponent touch the ball
                if (prev_state[21] * state[21]) <= 0:
                    # Calculate the trajectory to check if the ball go on our side of the table
                    x, y = trajectory(state)
                    if x is not None and y is not None:
                        # If the ball is going out (Run away)
                        if (x < -0.75 or x > 0.75) or (y < -0.2 or y > 1.2):
                            print("RUN")
                            action = get_neutral_joint_position()
                            out = True
                            if x <= 0:
                                action[1] = 0.8
                            else:
                                action[1] = -0.8
                        else:
                            x_max, y_max, z_max = max_height_point(state)

                            # if the ball is going in a good place with an high parable,
                            # wait the bounce to smash
                            if state[22] > 0 and y > 0.2 and z_max is not None and z_max >= 0.75:
                                wait_bounce_to_smash = True
                                action = get_neutral_joint_position()
                            else:
                                wait_bounce_to_smash = False

                        # if the ball is not going out, and we have decided to not smash
                        # and we don't have decided a stance yet
                        if not out and not wait_bounce_to_smash and not stance_chosen:

                            print("DON'T WAIT!")

                            stance_chosen = True

                            # We apply an offset on y in order to arrive to an optimal position,
                            # cause of the inclination of the paddle during the supervised train of
                            # the arm (0 angle)
                            input_state_arm[0] = y + 0.2
                            input_state_arm[1] = 0.5

                            input_state_arm = torch.Tensor(input_state_arm)

                            arm_action = arm_model(input_state_arm)
                            action[0] = arm_action[0]
                            action[1] = x
                            action[3] = arm_action[1]
                            action[5] = arm_action[2]
                            action[7] = arm_action[3]
                            action[9] = 1.1

                # if the ball is not going out, and we have decided to smash
                # and we don't have decided a stance yet and the ball is bounced
                if not out and wait_bounce_to_smash and prev_state[22] < 0 and state[22] > 0 and prev_state[18] < 1.2 and not stance_chosen:

                    # Scan the z to found the higher place to smash
                    x_smash, y_smash, z_smash = max_height_point(state)
                    while not stance_chosen:
                        if x_smash is not None and y_smash is not None:
                            if y_smash <= 0.2:
                                stance_chosen = True
                                # We apply an offset on y and z in order to arrive to an optimal position,
                                # cause of the inclination of the paddle during the supervised train of
                                # the arm (0 angle)
                                y = y_smash + 0.15
                                x = x_smash
                                z = z_smash - 0.4
                        else:
                            z_smash -= 0.05
                            x_smash, y_smash = trajectory(state, z_smash)

                    print("SMASH!")

                    input_state_arm[0] = y
                    input_state_arm[1] = z

                    input_state_arm = torch.Tensor(input_state_arm)

                    arm_action = arm_model(input_state_arm)
                    action[0] = arm_action[0]
                    action[1] = x
                    action[3] = arm_action[1]
                    action[5] = arm_action[2]
                    action[7] = arm_action[3]
                    # Calculate the z with this function in order to change the angle in function of
                    # the z to obtain the paddle perpendicular to the table
                    action[9] = (- 2.3) + ((z**2) * 1.3)

                """Training code"""
                # Take the paddle and ball position to calculate the Euclidean distance,
                # in order to activate the paddle networks
                paddle_pos = np.array(state[11:14])
                ball_pos = np.array(state[17:20])

                distance = np.linalg.norm(paddle_pos - ball_pos)

                # flag for the final state
                done = False

                # Activation of the paddle networks if the ball is near the paddle
                if distance <= 0.3 and stance_chosen:

                    if not wait_bounce_to_smash and distance <= 0.2:

                        # Build the input for the paddle agent
                        for i in range(6):
                            input_state_paddle[i] = state[i + 17]

                        input_state_paddle = torch.Tensor(input_state_paddle).to(device, dtype=torch.float32)

                        # Calculate the action from the agent
                        dont_wait_action = dont_wait_agent.calc_action(input_state_paddle, ou_noise_dont_wait)

                        action[9] = 1.1 - dont_wait_action[0]
                        action[10] = dont_wait_action[1]

                        # Apply the agent decision
                        cli.send_joints(action)

                        # Save the previous state
                        prev_state = state

                        # Take the new state until the paddle catch or miss the ball
                        while True:

                            state = cli.get_state()

                            paddle_pos = np.array(state[11:14])
                            ball_pos = np.array(state[17:20])

                            distance = np.linalg.norm(paddle_pos - ball_pos)

                            if distance > 0.2 or state[21] > 0:
                                break

                        # Save the state to calculate the reward
                        reward_state = state

                        if prev_state[34] == state[34] and prev_state[35] == state[35]:
                            # Wait for the game to finish and update the scores
                            # to use the score for the reward
                            our_score = state[34]
                            opponent_score = state[35]

                            while True:
                                state = cli.get_state()
                                if state[34] != our_score or state[35] != opponent_score:
                                    point_state = state
                                    break

                            done = True
                        else:
                            point_state = state
                            done = True

                        # Compute the reward
                        reward = calculate_paddle_reward(prev_state, reward_state, point_state)

                        print("Reward dont_wait: ", reward)

                        next_input_state_paddle = np.zeros(NUM_INPUTS_PADDLE, dtype=np.float32)

                        for i in range(6):
                            next_input_state_paddle[i] = state[i + 17]

                        tr_dw += 1
                        print("DW Transition registered: ", tr_dw)

                        #  Push the episode in the buffer
                        next_input_state_paddle = torch.Tensor(next_input_state_paddle).to(device, dtype=torch.float32)
                        dont_wait_action = torch.Tensor(dont_wait_action).to(device, dtype=torch.float32)
                        mask = torch.Tensor([done]).to(device, dtype=torch.float32)
                        reward = torch.Tensor([reward]).to(device, dtype=torch.float32)

                        dont_wait_memory.push(input_state_paddle, dont_wait_action, mask, next_input_state_paddle, reward)

                    if wait_bounce_to_smash:

                        # Build the input for the paddle agent
                        for i in range(6):
                            input_state_paddle[i] = state[i + 17]

                        input_state_paddle = torch.Tensor(input_state_paddle).to(device, dtype=torch.float32)

                        # Calculate the action from the agent
                        smash_action = smash_agent.calc_action(input_state_paddle, ou_noise_smash)

                        action[9] = (- 2.3) + ((z**2) * 1.3) + smash_action[0]
                        action[10] = smash_action[1]

                        # Apply the agent decision
                        cli.send_joints(action)

                        # Save the previous state
                        prev_state = state

                        # Take the new state until the paddle catch or miss the ball
                        while True:

                            state = cli.get_state()

                            paddle_pos = np.array(state[11:14])
                            ball_pos = np.array(state[17:20])

                            distance = np.linalg.norm(paddle_pos - ball_pos)

                            if distance > 0.3 or state[21] > 0:
                                break

                        # Save the state to calculate the reward
                        reward_state = state

                        if prev_state[34] == state[34] and prev_state[35] == state[35]:
                            # Wait for the game to finish and update the scores
                            # to use the score for the reward
                            our_score = state[34]
                            opponent_score = state[35]

                            while True:
                                state = cli.get_state()
                                if state[34] != our_score or state[35] != opponent_score:
                                    point_state = state
                                    break

                            done = True
                        else:
                            point_state = state
                            done = True

                        # Compute the reward
                        reward = calculate_paddle_reward(prev_state, reward_state, point_state)

                        print("Reward smash: ", reward)

                        next_input_state_paddle = np.zeros(NUM_INPUTS_PADDLE)

                        for i in range(6):
                            next_input_state_paddle[i] = state[i + 17]

                        tr_s += 1

                        #  Push the episode in the buffer
                        print("S Transition registered: ", tr_s)
                        next_input_state_paddle = torch.Tensor(next_input_state_paddle).to(device, dtype=torch.float32)
                        smash_action = torch.Tensor(smash_action).to(device, dtype=torch.float32)
                        mask = torch.Tensor([done]).to(device, dtype=torch.float32)
                        reward = torch.Tensor([reward]).to(device, dtype=torch.float32)

                        smash_memory.push(input_state_paddle, smash_action, mask, next_input_state_paddle, reward)

            cli.send_joints(action)
            prev_state = state

        # training start for the smash agent
        if tr_s >= REPLAY_SIZE_SMASH:
            epoch = 0
            while epoch < TOTAL_EPOCH:
                count_batch = 0
                print("Epoch Smash: ", epoch)
                while count_batch < BATCH_S:
                    transitions = smash_memory.sample(1)
                    batch = Transition(*zip(*transitions))

                    # Update actor and critic according to the batch
                    smash_agent.update_params(batch)
                    count_batch += 1
                    # print("Batch: ", count_batch)
                epoch += 1
                hard_s += 1
            model_number_s += epoch

        # training start for the don't wait agent
        if tr_dw >= REPLAY_SIZE_DW:
            epoch = 0
            while epoch < TOTAL_EPOCH:
                count_batch = 0
                print("Epoch Don't wait: ", epoch)
                while count_batch < BATCH_DW:
                    transitions = dont_wait_memory.sample(1)
                    batch = Transition(*zip(*transitions))

                    # Update actor and critic according to the batch
                    dont_wait_agent.update_params(batch)
                    count_batch += 1
                    # print("Batch: ", count_batch)
                epoch += 1
                hard_dw += 1
            model_number_dw += epoch

        # Hard update for the smash target networks each 20 epochs
        if hard_s == 20:
            hard_s = 0
            hard_update(smash_agent.actor_target, smash_agent.actor)
            hard_update(smash_agent.critic_target, smash_agent.critic)

        # Hard update for the don't wait target networks each 20 epochs
        if hard_dw == 20:
            hard_dw = 0
            hard_update(dont_wait_agent.actor_target, dont_wait_agent.actor)
            hard_update(dont_wait_agent.critic_target, dont_wait_agent.critic)

        # Save the smash model each 10 epochs
        if (model_number_s != 0) and (model_number_s % 10) == 0:
            smash_agent.save_checkpoint(model_number_s, "smash_agent")
            print("Saved smash_agent at epoch: ", model_number_s)

        # Save the don't wait model each 10 epochs
        if (model_number_dw != 0) and (model_number_dw % 10) == 0:
            dont_wait_agent.save_checkpoint(model_number_dw, "dont_wait_agent")
            print("Saved dont_wait_agent at epoch: ", model_number_dw)


def main():
    name = 'Paddles Train'
    if len(sys.argv) > 1:
        name = sys.argv[1]

    port = DEFAULT_PORT
    if len(sys.argv) > 2:
        port = sys.argv[2]

    host = 'localhost'
    if len(sys.argv) > 3:
        host = sys.argv[3]

    cli = Client(name, host, port)
    run(cli)


if __name__ == '__main__':
    '''
    python paddle_train.py name port host
    Default parameters:
     name: 'Example Client'
     port: client.DEFAULT_PORT
     host: 'localhost'

    To run the one simulation on the server, run this in 3 separate command shells:
    > python paddle_train.py player_A
    > python paddle_train.py player_B
    > python server.py

    To run a second simulation, select a different PORT on the server:
    > python paddle_train.py player_A 9544
    > python paddle_train.py player_B 9544
    > python server.py -port 9544    
    '''

    main()

